from __future__ import annotations

import base64
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlparse

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


_TIKTOK_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)
_GENERIC_TIKTOK_TEXT = {
    "",
    "tiktok",
    "tiktok - make your day",
}

_TIKTOK_MEDIA_HOST_HINTS = (
    "tiktokcdn.com",
    "tiktokcdn-us.com",
    "byteoversea.com",
    "byteoversea.net",
    "ibytedtos.com",
)
_TIKTOK_SCREENSHOT_TIMEOUT_MS = max(
    1000,
    min(15000, int((os.getenv("TIKTOK_SCREENSHOT_TIMEOUT_MS") or "5000").strip() or "5000")),
)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def _normalize_tiktok_media_url(media_url: str, page_url: str = "") -> str:
    candidate = _clean_text(media_url)
    if not candidate:
        return ""
    if candidate.startswith("//"):
        candidate = f"https:{candidate}"
    elif page_url and not urlparse(candidate).scheme:
        candidate = urljoin(page_url, candidate)
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"}:
        return ""
    return candidate


def _is_likely_tiktok_media_url(candidate: str) -> bool:
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    if not any(hint in host for hint in _TIKTOK_MEDIA_HOST_HINTS):
        return False
    return any(
        snippet in path
        for snippet in [
            ".mp4",
            "/video/",
            "/playwm/",
            "/play/",
            "/aweme/",
        ]
    )


def _extract_tiktok_media_candidates_from_html(html: str, page_url: str = "") -> list[str]:
    raw = html or ""
    matches = re.findall(
        r'(?:"(?:playAddr|downloadAddr|src|videoUrl)"\s*:\s*"([^"]+)"|https?:\\\\/\\\\/[^"\']+|//[^"\']+tiktokcdn[^"\']+)',
        raw,
        flags=re.IGNORECASE,
    )
    candidates: list[str] = []
    for match in matches:
        value = match if isinstance(match, str) else ""
        if not value:
            continue
        decoded = value.replace("\\u002F", "/").replace("\\/", "/").replace("\\u0026", "&")
        normalized = _normalize_tiktok_media_url(decoded, page_url)
        if normalized and _is_likely_tiktok_media_url(normalized):
            candidates.append(normalized)
    return _dedupe_preserve_order(candidates)


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        cleaned = _clean_text(value)
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        out.append(cleaned)
    return out


def _resolve_tiktok_cookiefile() -> str | None:
    cookie_path = (os.getenv("TIKTOK_COOKIE_FILE") or "").strip()
    if cookie_path and Path(cookie_path).exists():
        return cookie_path

    cookie_b64 = (os.getenv("TIKTOK_COOKIES_B64") or "").strip()
    if cookie_b64:
        try:
            decoded = base64.b64decode(cookie_b64).decode("utf-8", errors="ignore").strip()
            if decoded:
                fd, tmp_path = tempfile.mkstemp(prefix="tiktok_cookies_", suffix=".txt")
                os.close(fd)
                Path(tmp_path).write_text(decoded + "\n")
                return tmp_path
        except Exception:
            add_debug_note("Failed to decode TIKTOK_COOKIES_B64.")
            return None

    generic_cookie_path = (os.getenv("YTDLP_COOKIE_FILE") or "").strip()
    if generic_cookie_path and Path(generic_cookie_path).exists():
        return generic_cookie_path
    return None


def _tiktok_playwright_cookies(cookie_file: str | None) -> list[dict]:
    if not cookie_file:
        return []
    cookies = _playwright_cookies_from_netscape_file(cookie_file)
    return [
        cookie
        for cookie in cookies
        if any(
            token in str(cookie.get("domain") or "").lower()
            for token in ("tiktok.com", "tiktokcdn.com", "byteoversea.com", "ibyteimg.com")
        )
    ]


def _meta_content(soup: BeautifulSoup, attr_name: str, attr_value: str) -> str:
    node = soup.find("meta", attrs={attr_name: attr_value})
    if not node:
        return ""
    return _clean_text(str(node.get("content") or ""))


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


def _clean_tiktok_boilerplate(value: str) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    lowered = text.lower()
    if lowered in _GENERIC_TIKTOK_TEXT:
        return ""

    text = re.sub(r"\s*\|\s*tiktok\.?$", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"^watch more trending videos on tiktok\.?\s*", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"^tiktok:\s*", "", text, flags=re.IGNORECASE).strip()
    return "" if text.lower() in _GENERIC_TIKTOK_TEXT else text


def _looks_like_tiktok_app_shell(html: str) -> bool:
    raw = (html or "").lower()
    if not raw:
        return False
    return (
        ("<title>tiktok - make your day</title>" in raw or "<title>tiktok</title>" in raw)
        and "og:description" not in raw
        and "og:title" not in raw
    )


def extract_tiktok_metadata_from_html(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html or "", "html.parser")
    title_tag = _clean_tiktok_boilerplate(soup.title.get_text(" ", strip=True) if soup.title else "")
    og_title = _clean_tiktok_boilerplate(_meta_content(soup, "property", "og:title"))
    og_desc = _clean_tiktok_boilerplate(_meta_content(soup, "property", "og:description"))
    meta_desc = _clean_tiktok_boilerplate(_meta_content(soup, "name", "description"))
    author = _clean_tiktok_boilerplate(_meta_content(soup, "property", "og:site_name"))

    ld_title = ""
    ld_desc = ""
    ld_author = ""
    for block in _json_ld_blocks(soup):
        author_obj = block.get("author")
        if isinstance(author_obj, dict):
            ld_author = _clean_tiktok_boilerplate(str(author_obj.get("name") or "")) or ld_author
        elif isinstance(author_obj, str):
            ld_author = _clean_tiktok_boilerplate(author_obj) or ld_author
        ld_title = ld_title or _clean_tiktok_boilerplate(str(block.get("name") or block.get("headline") or ""))
        ld_desc = ld_desc or _clean_tiktok_boilerplate(
            str(block.get("description") or block.get("articleBody") or "")
        )

    title = next((value for value in [og_title, ld_title, title_tag] if value), "")
    description = next((value for value in [og_desc, meta_desc, ld_desc] if value), "")
    author = next((value for value in [author, ld_author] if value), "")
    return {
        "title": title,
        "description": description,
        "author": author.lstrip("@"),
    }


async def _fetch_tiktok_html(url: str) -> str:
    headers = {
        "User-Agent": _TIKTOK_USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
    }
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=headers) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


async def _fetch_tiktok_oembed(url: str) -> dict[str, str]:
    endpoint = "https://www.tiktok.com/oembed"
    headers = {"User-Agent": _TIKTOK_USER_AGENT}
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=headers) as client:
            resp = await client.get(endpoint, params={"url": url})
            resp.raise_for_status()
            data = resp.json()
        return {
            "title": _clean_tiktok_boilerplate(str(data.get("title") or "")),
            "description": _clean_tiktok_boilerplate(str(data.get("author_name") or "")),
            "author": _clean_tiktok_boilerplate(str(data.get("author_name") or "")),
        }
    except Exception as exc:
        add_debug_note(f"TikTok oEmbed fetch failed: {_short_exc(exc)}")
        return {}


def _interesting_rendered_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in re.split(r"[\r\n]+", text or ""):
        line = _clean_tiktok_boilerplate(raw_line)
        token_count = len(tokenize(line))
        lowered = line.lower()
        if token_count < 4:
            continue
        if any(
            snippet in lowered
            for snippet in [
                "log in",
                "sign up",
                "for you",
                "following",
                "friends",
                "upload",
                "explore more videos",
                "watch more trending videos",
            ]
        ):
            continue
        lines.append(line)
    return _dedupe_preserve_order(lines)


def _tiktok_should_use_browser_fallback(caption: str, transcript: str, shell_detected: bool) -> bool:
    if shell_detected:
        return True
    transcript_tokens = len(tokenize(transcript))
    if transcript_tokens >= 20:
        return False
    caption_tokens = len(tokenize(caption))
    return caption_tokens < 40


def _tiktok_can_skip_deep_media_recovery(caption: str, transcript: str) -> bool:
    if (os.getenv("TIKTOK_FAST_MODE") or "1").strip().lower() in {"0", "false", "no", "off"}:
        return False
    total_tokens = len(tokenize(f"{caption}\n{transcript}"))
    transcript_tokens = len(tokenize(transcript))
    min_total = max(40, int((os.getenv("TIKTOK_FAST_MODE_MIN_TOKENS") or "70").strip() or "70"))
    min_transcript = max(
        0, int((os.getenv("TIKTOK_FAST_MODE_MIN_TRANSCRIPT_TOKENS") or "20").strip() or "20")
    )
    return total_tokens >= min_total or transcript_tokens >= min_transcript


async def _download_tiktok_video(video_url: str, cookie_file: str | None = None) -> str | None:
    video_url = _normalize_tiktok_media_url(video_url)
    if not video_url:
        add_debug_note("TikTok video download skipped: media URL was empty or non-http.")
        return None
    headers = {
        "User-Agent": _TIKTOK_USER_AGENT,
        "Referer": "https://www.tiktok.com/",
    }
    cookies = _tiktok_playwright_cookies(cookie_file)
    cookie_jar = httpx.Cookies()
    for cookie in cookies:
        name = str(cookie.get("name") or "")
        value = str(cookie.get("value") or "")
        domain = str(cookie.get("domain") or "")
        if name and value:
            cookie_jar.set(name, value, domain=domain or None, path=str(cookie.get("path") or "/"))
    try:
        with tempfile.TemporaryDirectory() as td:
            out_path = Path(td) / "tiktok_video.mp4"
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
                kept_path = Path(tempfile.mkdtemp(prefix="tiktok_media_")) / out_path.name
                kept_path.write_bytes(out_path.read_bytes())
                return str(kept_path)
    except Exception as exc:
        add_debug_note(f"TikTok video download failed: {_short_exc(exc)}")
    return None


async def _browser_tiktok_fallback(url: str) -> tuple[str, str, str, str, list[str]]:
    if async_playwright is None:
        add_debug_note("TikTok browser ingestion requested but playwright is not installed.")
        return "", "", "", "", []

    timeout_sec = max(8, min(120, int((os.getenv("TIKTOK_BROWSER_TIMEOUT_SEC") or "25").strip() or "25")))
    timeout_ms = timeout_sec * 1000
    headless = (os.getenv("TIKTOK_BROWSER_HEADLESS") or "1").strip().lower() not in {"0", "false", "no", "off"}
    cookie_file = _resolve_tiktok_cookiefile()
    cookies = _tiktok_playwright_cookies(cookie_file)

    title = ""
    description = ""
    transcript = ""
    video_url = ""
    notes: list[str] = []
    shell_detected = False
    discovered_media_urls: list[str] = []
    authenticated = bool(cookies)

    try:
        async with _shared_playwright_context(
            headless=headless,
            user_agent=_TIKTOK_USER_AGENT,
            locale="en-US",
            viewport={"width": 1280, "height": 1600},
            device_scale_factor=1.0,
            cookies=cookies,
            launch_args=["--no-sandbox", "--disable-dev-shm-usage"],
        ) as context:
            async def _route_handler(route) -> None:
                try:
                    if route.request.resource_type == "font":
                        await route.abort()
                    else:
                        await route.continue_()
                except Exception:
                    try:
                        await route.continue_()
                    except Exception:
                        pass

            await context.route("**/*", _route_handler)
            page = await context.new_page()

            def _remember_media(candidate: str) -> None:
                normalized = _normalize_tiktok_media_url(candidate, url)
                if normalized and _is_likely_tiktok_media_url(normalized):
                    discovered_media_urls.append(normalized)

            page.on(
                "response",
                lambda response: _remember_media(response.url),
            )
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            await page.wait_for_timeout(1200)

            payload = await page.evaluate(
                """() => {
                    const ogTitle = document.querySelector('meta[property="og:title"]')?.content || '';
                    const ogDesc = document.querySelector('meta[property="og:description"]')?.content || '';
                    const bodyText = document.body?.innerText || '';
                    const mainText = document.querySelector('main')?.innerText || '';
                    const video = document.querySelector('video');
                    const sourceUrls = Array.from(document.querySelectorAll('video source'))
                      .map((node) => node?.src || '')
                      .filter(Boolean);
                    return {
                        title: ogTitle || document.title || '',
                        description: ogDesc || '',
                        bodyText,
                        mainText,
                        videoUrl: video?.currentSrc || video?.src || '',
                        sourceUrls,
                    };
                }"""
            )

            if isinstance(payload, dict):
                title = _clean_tiktok_boilerplate(str(payload.get("title") or ""))
                description = _clean_tiktok_boilerplate(str(payload.get("description") or ""))
                payload_media_urls = [
                    str(payload.get("videoUrl") or "").strip(),
                    *[str(item or "").strip() for item in (payload.get("sourceUrls") or [])],
                ]
                for candidate in payload_media_urls:
                    _remember_media(candidate)
                lines = _interesting_rendered_lines(
                    "\n".join([str(payload.get("mainText") or ""), str(payload.get("bodyText") or "")])
                )
                if lines:
                    best = sorted(lines, key=lambda value: len(tokenize(value)), reverse=True)
                    if not description:
                        description = best[0]
                    elif best[0] != description:
                        description = "\n\n".join(_dedupe_preserve_order([description, best[0]])[:2]).strip()

            html = await page.content()
            shell_detected = _looks_like_tiktok_app_shell(html)
            for candidate in _extract_tiktok_media_candidates_from_html(html, url):
                _remember_media(candidate)

            ocr_samples = max(1, min(8, int((os.getenv("TIKTOK_BROWSER_OCR_SAMPLES") or "2").strip() or "2")))
            snippets: list[str] = []
            for _ in range(ocr_samples):
                try:
                    png = await page.screenshot(
                        full_page=False,
                        type="png",
                        timeout=_TIKTOK_SCREENSHOT_TIMEOUT_MS,
                        animations="disabled",
                    )
                    text = _clean_ocr_text(_ocr_browser_subtitle_text(png))
                    if text:
                        snippets.append(text)
                except Exception as exc:
                    add_debug_note(f"TikTok browser screenshot OCR sample failed: {_short_exc(exc)}")
                    break
                await page.wait_for_timeout(450)
            if snippets:
                transcript = " ".join(_dedupe_preserve_order(snippets)).strip()

            if discovered_media_urls:
                video_url = discovered_media_urls[0]

    except Exception as exc:
        add_debug_note(f"TikTok browser ingestion failed: {_short_exc(exc)}")
        return "", "", "", "", notes

    if title or description:
        notes.append("Recovered TikTok metadata from browser-rendered page.")
    if transcript:
        notes.append("Recovered TikTok on-screen text from browser screenshots.")
    if shell_detected:
        notes.append("Browser-rendered TikTok page still resolved to a generic shell.")
    if authenticated:
        notes.append("TikTok browser ingestion used worker cookies/session.")
    else:
        notes.append("TikTok browser ingestion ran without authenticated cookies.")
    return title, description, transcript, video_url, notes


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


async def enrich_from_tiktok(
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
        html = await _fetch_tiktok_html(url)
        shell_detected = _looks_like_tiktok_app_shell(html)
        meta = extract_tiktok_metadata_from_html(html)
        title = meta.get("title", "")
        description = meta.get("description", "")
        author = meta.get("author", "")
        caption = "\n\n".join(_dedupe_preserve_order([title, description])).strip()
        if len(tokenize(caption)) < 12:
            oembed = await _fetch_tiktok_oembed(url)
            improved = "\n\n".join(
                _dedupe_preserve_order([caption, oembed.get("title", ""), oembed.get("description", "")])
            ).strip()
            if len(tokenize(improved)) > len(tokenize(caption)):
                caption = improved
                author = author or oembed.get("author", "")
                notes.append("Expanded TikTok metadata using public oEmbed.")
        if caption:
            if author:
                notes.append(f"Auto-ingested TikTok metadata (author: {author}).")
            else:
                notes.append("Auto-ingested TikTok metadata from page markup.")
        else:
            if shell_detected:
                notes.append("TikTok returned a generic shell page instead of public post metadata.")
            else:
                notes.append("TikTok page fetched, but no useful metadata text was extracted.")
        notes.append("TikTok transcript auto-ingestion is limited; using metadata-first analysis.")
    except Exception as exc:
        add_debug_note(f"TikTok metadata fetch failed: {_short_exc(exc)}")
        notes.append("Could not fetch TikTok metadata from the public page.")

    if heavy_ingestion_enabled and _tiktok_should_use_browser_fallback(caption, transcript, shell_detected):
        browser_title, browser_desc, browser_transcript, video_url, browser_notes = await _browser_tiktok_fallback(url)
        cookie_file = _resolve_tiktok_cookiefile()
        authenticated = bool(_tiktok_playwright_cookies(cookie_file))
        browser_caption = "\n\n".join(_dedupe_preserve_order([caption, browser_title, browser_desc])).strip()
        if len(tokenize(browser_caption)) > len(tokenize(caption)):
            caption = browser_caption
        if browser_transcript:
            transcript = f"{transcript}\n\n{browser_transcript}".strip() if transcript else browser_transcript
            ocr_tokens += len(tokenize(browser_transcript))
        notes.extend(browser_notes)

        skip_deep_media = _tiktok_can_skip_deep_media_recovery(caption, transcript)
        if skip_deep_media:
            notes.append(
                "Skipped deeper TikTok media recovery because browser evidence already reached target coverage."
            )
        if worker_mode and video_url and not skip_deep_media:
            downloaded_video = await _download_tiktok_video(video_url, cookie_file)
            if downloaded_video:
                notes.append("Worker downloaded TikTok video from browser-discovered media URL.")
                local_asr = await _local_asr_from_video(downloaded_video)
                if local_asr:
                    transcript = f"{transcript}\n\n{local_asr}".strip() if transcript else local_asr
                    asr_tokens += len(tokenize(local_asr))
                    notes.append("Recovered TikTok speech transcript with local Whisper ASR.")
                frame_ocr, signal_text, frame_notes = await _scan_frames_from_video(downloaded_video)
                if frame_ocr:
                    transcript = f"{transcript}\n\n{frame_ocr}".strip() if transcript else frame_ocr
                    ocr_tokens += len(tokenize(frame_ocr))
                    notes.append("Recovered TikTok on-screen text via frame-by-frame OCR scan.")
                if signal_text:
                    transcript = f"{transcript}\n\n{signal_text}".strip() if transcript else signal_text
                notes.extend(frame_notes[:4])
            else:
                if authenticated:
                    notes.append("TikTok worker found a media URL but could not download the video with current cookies.")
                else:
                    notes.append("TikTok worker found a media URL but could not download the video.")

        if shell_detected and not caption and not transcript and not authenticated:
            notes.append("TikTok likely requires an authenticated worker session for richer ingest on this link.")

    if include_evidence:
        return caption, transcript, notes, _build_evidence(caption, transcript, ocr_tokens, asr_tokens)
    return caption, transcript, notes
