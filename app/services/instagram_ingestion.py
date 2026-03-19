from __future__ import annotations

import base64
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.services.debug_state import add_debug_note
from app.services.ingestion import (
    _allow_heavy_ingestion,
    _clean_ocr_text,
    _local_asr_from_video,
    _ocr_browser_subtitle_text,
    _playwright_cookies_from_netscape_file,
    _scan_frames_from_video,
    _short_exc,
    _shared_playwright_context,
    async_playwright,
)
from app.services.retrieval import tokenize


_INSTAGRAM_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)
_INSTAGRAM_APP_ID = "936619743392459"
_GENERIC_INSTAGRAM_TEXT = {
    "",
    "instagram",
    "instagram photos and videos",
    "login instagram",
    "login • instagram",
}


def _instagram_should_use_browser_fallback(caption: str, transcript: str, shell_detected: bool) -> bool:
    if shell_detected:
        return True
    transcript_tokens = len(tokenize(transcript))
    if transcript_tokens >= 20:
        return False
    caption_tokens = len(tokenize(caption))
    return caption_tokens < 40


def _instagram_can_skip_deep_media_recovery(caption: str, transcript: str) -> bool:
    if (os.getenv("INSTAGRAM_FAST_MODE") or "1").strip().lower() in {"0", "false", "no", "off"}:
        return False
    total_tokens = len(tokenize(f"{caption}\n{transcript}"))
    transcript_tokens = len(tokenize(transcript))
    min_total = max(40, int((os.getenv("INSTAGRAM_FAST_MODE_MIN_TOKENS") or "120").strip() or "120"))
    min_transcript = max(
        0, int((os.getenv("INSTAGRAM_FAST_MODE_MIN_TRANSCRIPT_TOKENS") or "40").strip() or "40")
    )
    return total_tokens >= min_total or transcript_tokens >= min_transcript


def _clean_text(value: str) -> str:
    text = re.sub(r"\s+", " ", (value or "")).strip()
    return text


def _first_nonempty(*values: str) -> str:
    for value in values:
        cleaned = _clean_text(value)
        if cleaned:
            return cleaned
    return ""


def _meta_content(soup: BeautifulSoup, attr_name: str, attr_value: str) -> str:
    node = soup.find("meta", attrs={attr_name: attr_value})
    if not node:
        return ""
    return _clean_text(str(node.get("content") or ""))


def _decode_json_string(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    try:
        return _clean_text(json.loads(f'"{raw}"'))
    except Exception:
        return _clean_text(raw.encode("utf-8", errors="ignore").decode("unicode_escape"))


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        cleaned = _clean_text(value)
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(cleaned)
    return deduped


def _clean_instagram_boilerplate(value: str) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    lowered = text.lower()
    if lowered in _GENERIC_INSTAGRAM_TEXT:
        return ""

    replacements = [
        (r"^watch this reel by\s+", ""),
        (r"^watch more from\s+", ""),
        (r"^post by\s+@", "@"),
        (r"\s+on instagram\.?$", ""),
        (r"^instagram:\s*", ""),
        (r"^\d[\d,\.]*\s+likes?,?\s*\d*[\d,\.]*\s*comments?\s*-\s*", ""),
    ]
    for pattern, repl in replacements:
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE).strip()

    if ":" in text:
        prefix, suffix = text.split(":", 1)
        if any(marker in prefix.lower() for marker in ["likes", "comments", "reel", "post by", "@"]):
            text = suffix.strip()

    lowered = text.lower()
    if lowered in _GENERIC_INSTAGRAM_TEXT:
        return ""
    return text


def _json_ld_blocks(soup: BeautifulSoup) -> list[dict]:
    blocks: list[dict] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except Exception:
            continue
        if isinstance(parsed, dict):
            blocks.append(parsed)
        elif isinstance(parsed, list):
            blocks.extend(item for item in parsed if isinstance(item, dict))
    return blocks


def _extract_regex_group(html: str, pattern: str) -> str:
    match = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return _decode_json_string(match.group(1))


def _extract_instagram_json_hints(html: str) -> dict[str, str]:
    patterns = {
        "caption": [
            r'"edge_media_to_caption"\s*:\s*\{"edges"\s*:\s*\[\{"node"\s*:\s*\{"text"\s*:\s*"((?:\\.|[^"\\])*)"',
            r'"caption"\s*:\s*\{"text"\s*:\s*"((?:\\.|[^"\\])*)"',
            r'"accessibility_caption"\s*:\s*"((?:\\.|[^"\\])*)"',
            r'"accessibilityCaption"\s*:\s*"((?:\\.|[^"\\])*)"',
        ],
        "title": [
            r'"title"\s*:\s*"((?:\\.|[^"\\])*)"',
            r'"headline"\s*:\s*"((?:\\.|[^"\\])*)"',
        ],
        "author": [
            r'"owner"\s*:\s*\{[^{}]*"username"\s*:\s*"((?:\\.|[^"\\])*)"',
            r'"owner_username"\s*:\s*"((?:\\.|[^"\\])*)"',
            r'"username"\s*:\s*"((?:\\.|[^"\\])*)"',
        ],
    }
    extracted: dict[str, str] = {}
    for key, key_patterns in patterns.items():
        for pattern in key_patterns:
            value = _extract_regex_group(html, pattern)
            if value:
                extracted[key] = value
                break
    return extracted


def _extract_embed_caption_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    selectors = [
        "blockquote",
        "figcaption",
        "div.Caption",
        "div[data-testid='post-comment-root']",
        "main",
    ]
    candidates: list[str] = []
    for selector in selectors:
        for node in soup.select(selector):
            text = _clean_instagram_boilerplate(node.get_text(" ", strip=True))
            if text and len(tokenize(text)) >= 6:
                candidates.append(text)
    if not candidates:
        return ""
    candidates.sort(key=lambda value: len(tokenize(value)), reverse=True)
    return candidates[0]


def _looks_like_instagram_app_shell(html: str) -> bool:
    raw = (html or "").lower()
    if not raw:
        return False
    return (
        '"pageid":"httperrorpage"' in raw
        or '"pageid":"loginpage"' in raw
        or ('<title>instagram</title>' in raw and "og:description" not in raw and "og:title" not in raw)
    )


def extract_instagram_metadata_from_html(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html or "", "html.parser")
    json_hints = _extract_instagram_json_hints(html or "")
    title_tag = _clean_instagram_boilerplate(soup.title.get_text(" ", strip=True) if soup.title else "")
    og_title = _clean_instagram_boilerplate(_meta_content(soup, "property", "og:title"))
    og_desc = _clean_instagram_boilerplate(_meta_content(soup, "property", "og:description"))
    meta_desc = _clean_instagram_boilerplate(_meta_content(soup, "name", "description"))
    og_type = _meta_content(soup, "property", "og:type")

    ld_title = ""
    ld_desc = ""
    ld_author = ""
    for block in _json_ld_blocks(soup):
        author = block.get("author")
        if isinstance(author, dict):
            ld_author = _first_nonempty(ld_author, str(author.get("name") or ""))
        elif isinstance(author, str):
            ld_author = _first_nonempty(ld_author, author)
        ld_title = _first_nonempty(
            ld_title,
            str(block.get("headline") or ""),
            str(block.get("name") or ""),
        )
        ld_desc = _first_nonempty(
            ld_desc,
            str(block.get("description") or ""),
            str(block.get("articleBody") or ""),
            str(block.get("caption") or ""),
        )

    title = _first_nonempty(json_hints.get("title", ""), og_title, ld_title, title_tag)
    description = _first_nonempty(
        json_hints.get("caption", ""),
        og_desc,
        meta_desc,
        ld_desc,
        _extract_embed_caption_text(html or ""),
    )
    author = _first_nonempty(
        _meta_content(soup, "property", "instapp:owner_user_name"),
        json_hints.get("author", ""),
        ld_author,
    )

    return {
        "title": _clean_instagram_boilerplate(title),
        "description": _clean_instagram_boilerplate(description),
        "author": _clean_instagram_boilerplate(author).lstrip("@"),
        "og_type": og_type,
    }


def _instagram_embed_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    return f"https://www.instagram.com{path}/embed/captioned/"


def _resolve_instagram_cookiefile() -> str | None:
    cookie_path = (os.getenv("INSTAGRAM_COOKIE_FILE") or "").strip()
    if cookie_path and Path(cookie_path).exists():
        return cookie_path

    cookie_b64 = (os.getenv("INSTAGRAM_COOKIES_B64") or "").strip()
    if cookie_b64:
        try:
            decoded = base64.b64decode(cookie_b64).decode("utf-8", errors="ignore").strip()
            if decoded:
                fd, tmp_path = tempfile.mkstemp(prefix="instagram_cookies_", suffix=".txt")
                os.close(fd)
                Path(tmp_path).write_text(decoded + "\n")
                return tmp_path
        except Exception:
            add_debug_note("Failed to decode INSTAGRAM_COOKIES_B64.")
            return None

    generic_cookie_path = (os.getenv("YTDLP_COOKIE_FILE") or "").strip()
    if generic_cookie_path and Path(generic_cookie_path).exists():
        return generic_cookie_path
    return None


def _instagram_playwright_cookies(cookie_file: str | None) -> list[dict]:
    if not cookie_file:
        return []
    cookies = _playwright_cookies_from_netscape_file(cookie_file)
    filtered = [
        cookie
        for cookie in cookies
        if any(
            token in str(cookie.get("domain") or "").lower()
            for token in ("instagram.com", "cdninstagram.com", "fbcdn.net")
        )
    ]
    return filtered


def _interesting_rendered_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in re.split(r"[\r\n]+", text or ""):
        line = _clean_instagram_boilerplate(raw_line)
        token_count = len(tokenize(line))
        lowered = line.lower()
        if token_count < 4:
            continue
        if any(
            snippet in lowered
            for snippet in [
                "meta verified",
                "log in",
                "sign up",
                "see translation",
                "more posts from",
                "add a comment",
                "view all comments",
                "likes",
                "followers",
                "following",
            ]
        ):
            continue
        lines.append(line)
    return _dedupe_preserve_order(lines)


async def _download_instagram_video(video_url: str, cookie_file: str | None) -> str | None:
    headers = {
        "User-Agent": _INSTAGRAM_USER_AGENT,
        "Referer": "https://www.instagram.com/",
    }
    cookies = _instagram_playwright_cookies(cookie_file)
    cookie_jar = httpx.Cookies()
    for cookie in cookies:
        name = str(cookie.get("name") or "")
        value = str(cookie.get("value") or "")
        domain = str(cookie.get("domain") or "")
        if name and value:
            cookie_jar.set(name, value, domain=domain or None, path=str(cookie.get("path") or "/"))

    try:
        with tempfile.TemporaryDirectory() as td:
            out_path = Path(td) / "instagram_video.mp4"
            async with httpx.AsyncClient(
                timeout=60,
                follow_redirects=True,
                headers=headers,
                cookies=cookie_jar,
            ) as client:
                resp = await client.get(video_url)
                resp.raise_for_status()
                out_path.write_bytes(resp.content)
            if out_path.exists() and out_path.stat().st_size > 0:
                kept_path = Path(tempfile.mkdtemp(prefix="instagram_media_")) / out_path.name
                kept_path.write_bytes(out_path.read_bytes())
                return str(kept_path)
    except Exception as exc:
        add_debug_note(f"Instagram video download failed: {_short_exc(exc)}")
    return None


async def _browser_instagram_fallback(url: str) -> tuple[str, str, str, str, list[str], bool]:
    if async_playwright is None:
        add_debug_note("Instagram browser ingestion requested but playwright is not installed.")
        return "", "", "", "", [], False

    timeout_sec = max(8, min(120, int((os.getenv("INSTAGRAM_BROWSER_TIMEOUT_SEC") or "25").strip() or "25")))
    timeout_ms = timeout_sec * 1000
    headless = (os.getenv("INSTAGRAM_BROWSER_HEADLESS") or "1").strip().lower() not in {"0", "false", "no", "off"}
    cookie_file = _resolve_instagram_cookiefile()
    cookies = _instagram_playwright_cookies(cookie_file)

    title = ""
    description = ""
    transcript = ""
    video_url = ""
    notes: list[str] = []
    authenticated = bool(cookies)
    shell_detected = False

    try:
        async with _shared_playwright_context(
            headless=headless,
            user_agent=_INSTAGRAM_USER_AGENT,
            locale="en-US",
            viewport={"width": 1280, "height": 1600},
            device_scale_factor=1.0,
            cookies=cookies,
            launch_args=["--no-sandbox", "--disable-dev-shm-usage"],
        ) as context:
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            await page.wait_for_timeout(1200)

            payload = await page.evaluate(
                """() => {
                    const ogTitle = document.querySelector('meta[property="og:title"]')?.content || '';
                    const ogDesc = document.querySelector('meta[property="og:description"]')?.content || '';
                    const metaDesc = document.querySelector('meta[name="description"]')?.content || '';
                    const bodyText = document.body?.innerText || '';
                    const articleText = document.querySelector('article')?.innerText || '';
                    const mainText = document.querySelector('main')?.innerText || '';
                    const video = document.querySelector('video');
                    return {
                        title: ogTitle || document.title || '',
                        description: ogDesc || metaDesc || '',
                        articleText,
                        mainText,
                        bodyText,
                        videoUrl: video?.currentSrc || video?.src || '',
                        hasVideo: Boolean(video),
                    };
                }"""
            )

            if isinstance(payload, dict):
                title = _clean_instagram_boilerplate(str(payload.get("title") or ""))
                description = _clean_instagram_boilerplate(str(payload.get("description") or ""))
                video_url = str(payload.get("videoUrl") or "").strip()
                text_candidates = _interesting_rendered_lines(
                    "\n".join(
                        [
                            str(payload.get("articleText") or ""),
                            str(payload.get("mainText") or ""),
                            str(payload.get("bodyText") or ""),
                        ]
                    )
                )
                if text_candidates:
                    best = sorted(text_candidates, key=lambda value: len(tokenize(value)), reverse=True)
                    if not description:
                        description = best[0]
                    elif best[0] != description:
                        description = "\n\n".join(_dedupe_preserve_order([description, best[0]])[:2]).strip()

            html = await page.content()
            shell_detected = _looks_like_instagram_app_shell(html)

            ocr_samples = max(1, min(8, int((os.getenv("INSTAGRAM_BROWSER_OCR_SAMPLES") or "2").strip() or "2")))
            snippets: list[str] = []
            for _ in range(ocr_samples):
                png = await page.screenshot(full_page=False, type="png")
                text = _clean_ocr_text(_ocr_browser_subtitle_text(png))
                if text:
                    snippets.append(text)
                await page.wait_for_timeout(450)
            if snippets:
                transcript = " ".join(_dedupe_preserve_order(snippets)).strip()

    except Exception as exc:
        add_debug_note(f"Instagram browser ingestion failed: {_short_exc(exc)}")
        return "", "", "", "", notes, authenticated

    if title or description:
        notes.append("Recovered Instagram metadata from browser-rendered page.")
    if transcript:
        notes.append("Recovered Instagram on-screen text from browser screenshots.")
    if shell_detected:
        notes.append("Browser-rendered Instagram page still resolved to a generic app shell.")
    if authenticated:
        notes.append("Instagram browser ingestion used worker cookies/session.")
    else:
        notes.append("Instagram browser ingestion ran without authenticated cookies.")
    return title, description, transcript, video_url, notes, authenticated


async def _fetch_instagram_html(url: str) -> str:
    headers = {
        "User-Agent": _INSTAGRAM_USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
        "X-IG-App-ID": _INSTAGRAM_APP_ID,
    }
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=headers) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


def _build_evidence(caption: str, transcript: str, ocr_tokens: int = 0, asr_tokens: int = 0) -> dict:
    caption_tokens = len(tokenize(caption))
    transcript_tokens = len(tokenize(transcript))
    total_tokens = len(tokenize(f"{caption}\n{transcript}"))
    level = "high" if total_tokens >= 120 else "medium" if total_tokens >= 40 else "low"
    return {
        "total_tokens": int(total_tokens),
        "caption_tokens": int(caption_tokens),
        "transcript_tokens": int(transcript_tokens),
        "ocr_tokens": int(ocr_tokens),
        "asr_tokens": int(asr_tokens),
        "level": level,
        "transcript_present": bool(transcript.strip()),
        "ocr_present": ocr_tokens > 0,
        "asr_present": asr_tokens > 0,
    }


async def enrich_from_instagram(
    url: str, worker_mode: bool = False, include_evidence: bool = False
) -> tuple[str, str, list[str]] | tuple[str, str, list[str], dict]:
    notes: list[str] = []
    caption = ""
    transcript = ""
    ocr_tokens = 0
    asr_tokens = 0
    heavy_ingestion_enabled = _allow_heavy_ingestion(worker_mode)
    shell_detected = False

    try:
        html = await _fetch_instagram_html(url)
        shell_detected = _looks_like_instagram_app_shell(html)
        meta = extract_instagram_metadata_from_html(html)
        title = meta.get("title", "")
        description = meta.get("description", "")
        author = meta.get("author", "")
        parts = _dedupe_preserve_order([title, description])
        caption = "\n\n".join(parts).strip()
        if len(tokenize(caption)) < 12:
            try:
                embed_html = await _fetch_instagram_html(_instagram_embed_url(url))
                embed_meta = extract_instagram_metadata_from_html(embed_html)
                embed_parts = _dedupe_preserve_order(
                    [
                        caption,
                        embed_meta.get("title", ""),
                        embed_meta.get("description", ""),
                    ]
                )
                improved_caption = "\n\n".join(embed_parts).strip()
                if len(tokenize(improved_caption)) > len(tokenize(caption)):
                    caption = improved_caption
                    author = author or embed_meta.get("author", "")
                    notes.append("Expanded Instagram metadata using the public embed page.")
            except Exception as exc:
                add_debug_note(f"Instagram embed fetch failed: {type(exc).__name__}.")
        if caption:
            if author:
                notes.append(f"Auto-ingested Instagram metadata (author: {author}).")
            else:
                notes.append("Auto-ingested Instagram metadata from page markup.")
        else:
            if shell_detected:
                notes.append(
                    "Instagram returned a generic app-shell/error page instead of public post metadata."
                )
            else:
                notes.append("Instagram page fetched, but no useful metadata text was extracted.")
        notes.append("Instagram transcript auto-ingestion is not enabled yet; using metadata-only analysis.")
    except Exception as exc:
        add_debug_note(f"Instagram metadata fetch failed: {type(exc).__name__}.")
        notes.append("Could not fetch Instagram metadata from the public page.")

    if heavy_ingestion_enabled and _instagram_should_use_browser_fallback(caption, transcript, shell_detected):
        browser_title, browser_desc, browser_transcript, video_url, browser_notes, authenticated = (
            await _browser_instagram_fallback(url)
        )
        browser_caption = "\n\n".join(
            _dedupe_preserve_order([caption, browser_title, browser_desc])
        ).strip()
        if len(tokenize(browser_caption)) > len(tokenize(caption)):
            caption = browser_caption
        if browser_transcript:
            transcript = f"{transcript}\n\n{browser_transcript}".strip() if transcript else browser_transcript
            ocr_tokens += len(tokenize(browser_transcript))
        notes.extend(browser_notes)

        skip_deep_media = _instagram_can_skip_deep_media_recovery(caption, transcript)
        if skip_deep_media:
            notes.append(
                "Skipped deeper Instagram media recovery because browser evidence already reached target coverage."
            )
        if worker_mode and video_url and not skip_deep_media:
            downloaded_video = await _download_instagram_video(video_url, _resolve_instagram_cookiefile())
            if downloaded_video:
                notes.append("Worker downloaded Instagram video from browser-discovered media URL.")
                local_asr = await _local_asr_from_video(downloaded_video)
                if local_asr:
                    transcript = f"{transcript}\n\n{local_asr}".strip() if transcript else local_asr
                    asr_tokens += len(tokenize(local_asr))
                    notes.append("Recovered Instagram speech transcript with local Whisper ASR.")
                frame_ocr, signal_text, frame_notes = await _scan_frames_from_video(downloaded_video)
                if frame_ocr:
                    transcript = f"{transcript}\n\n{frame_ocr}".strip() if transcript else frame_ocr
                    ocr_tokens += len(tokenize(frame_ocr))
                    notes.append("Recovered Instagram on-screen text via frame-by-frame OCR scan.")
                if signal_text:
                    transcript = f"{transcript}\n\n{signal_text}".strip() if transcript else signal_text
                notes.extend(frame_notes[:4])
            elif authenticated:
                notes.append("Instagram worker found a media URL but could not download the video with current cookies.")

        if shell_detected and not caption and not transcript and not authenticated:
            notes.append("Instagram likely requires an authenticated worker session for richer ingest on this link.")

    if include_evidence:
        return caption, transcript, notes, _build_evidence(caption, transcript, ocr_tokens, asr_tokens)
    return caption, transcript, notes
