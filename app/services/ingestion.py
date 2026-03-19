from __future__ import annotations

import asyncio
import base64
import io
import json
import mimetypes
import os
import re
import subprocess
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup
from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat
from pytesseract import image_to_string
from yt_dlp import YoutubeDL
from youtube_transcript_api import YouTubeTranscriptApi

from app.services.debug_state import add_debug_note
from app.services.extractors import extract_signals
from app.services.retrieval import retrieve_evidence, tokenize

try:
    from faster_whisper import WhisperModel  # type: ignore
except Exception:
    WhisperModel = None  # type: ignore
try:
    from pytubefix import YouTube as PytubeYouTube  # type: ignore
except Exception:
    PytubeYouTube = None  # type: ignore
try:
    from playwright.async_api import async_playwright  # type: ignore
except Exception:
    async_playwright = None  # type: ignore

_WHISPER_MODEL = None
_YTDLP_COOKIEFILE_RESOLVED: str | None = None
_YTDLP_COOKIEFILE_INIT_ATTEMPTED = False
_YTDLP_PO_TOKENS: dict[str, str] | None = None
_YTDLP_PO_ENABLED: bool | None = None
_PLAYWRIGHT_LOCK: asyncio.Lock | None = None
_PLAYWRIGHT_INSTANCE = None
_PLAYWRIGHT_BROWSER = None
_PLAYWRIGHT_BROWSER_HEADLESS: bool | None = None
_PLAYWRIGHT_LAST_USED_TS = 0.0


class YouTubeHardBlockError(RuntimeError):
    pass


def _short_exc(exc: Exception, max_len: int = 220) -> str:
    msg = str(exc).strip().replace("\n", " ")
    if len(msg) > max_len:
        msg = msg[: max_len - 3] + "..."
    return f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__


def _is_youtube_hard_block_text(message: str) -> bool:
    msg = message.lower()
    hard_markers = [
        "sign in to confirm you’re not a bot",
        "sign in to confirm you're not a bot",
        "video unavailable",
        "private video",
        "this content isn't available",
    ]
    return any(marker in msg for marker in hard_markers)


def _is_truthy_env(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _allow_heavy_ingestion(worker_mode: bool) -> bool:
    # Keep Fly web process lightweight by default; heavy media work is for queue workers.
    if worker_mode:
        return True
    ingest_mode = (os.getenv("INGEST_MODE") or "").strip().lower()
    if ingest_mode == "rich":
        return True
    if ingest_mode in {"light", "lite", "metadata"}:
        return False
    return _is_truthy_env("ENABLE_HEAVY_INGESTION", default=False)


def _playwright_lock() -> asyncio.Lock:
    global _PLAYWRIGHT_LOCK
    if _PLAYWRIGHT_LOCK is None:
        _PLAYWRIGHT_LOCK = asyncio.Lock()
    return _PLAYWRIGHT_LOCK


def _playwright_idle_ttl_sec() -> int:
    raw = (os.getenv("PLAYWRIGHT_BROWSER_IDLE_TTL_SEC") or "180").strip()
    try:
        return max(30, min(3600, int(raw)))
    except Exception:
        return 180


async def _close_shared_playwright_browser() -> None:
    global _PLAYWRIGHT_BROWSER, _PLAYWRIGHT_INSTANCE, _PLAYWRIGHT_BROWSER_HEADLESS, _PLAYWRIGHT_LAST_USED_TS
    browser = _PLAYWRIGHT_BROWSER
    playwright_instance = _PLAYWRIGHT_INSTANCE
    _PLAYWRIGHT_BROWSER = None
    _PLAYWRIGHT_INSTANCE = None
    _PLAYWRIGHT_BROWSER_HEADLESS = None
    _PLAYWRIGHT_LAST_USED_TS = 0.0
    try:
        if browser is not None:
            await browser.close()
    except Exception:
        pass
    try:
        if playwright_instance is not None:
            await playwright_instance.stop()
    except Exception:
        pass


async def _get_shared_playwright_browser(headless: bool, launch_args: list[str] | None = None):
    global _PLAYWRIGHT_INSTANCE, _PLAYWRIGHT_BROWSER, _PLAYWRIGHT_BROWSER_HEADLESS, _PLAYWRIGHT_LAST_USED_TS

    if async_playwright is None:
        return None

    async with _playwright_lock():
        now = time.monotonic()
        ttl = _playwright_idle_ttl_sec()
        if (
            _PLAYWRIGHT_BROWSER is not None
            and _PLAYWRIGHT_BROWSER_HEADLESS == headless
            and _PLAYWRIGHT_LAST_USED_TS
            and (now - _PLAYWRIGHT_LAST_USED_TS) > ttl
        ):
            add_debug_note("Recycling idle shared Playwright browser.")
            await _close_shared_playwright_browser()

        if _PLAYWRIGHT_BROWSER is None or _PLAYWRIGHT_BROWSER_HEADLESS != headless:
            if _PLAYWRIGHT_BROWSER is not None:
                await _close_shared_playwright_browser()
            _PLAYWRIGHT_INSTANCE = await async_playwright().start()
            _PLAYWRIGHT_BROWSER = await _PLAYWRIGHT_INSTANCE.chromium.launch(
                headless=headless,
                args=launch_args or ["--no-sandbox", "--disable-dev-shm-usage"],
            )
            _PLAYWRIGHT_BROWSER_HEADLESS = headless
            add_debug_note("Initialized shared Playwright Chromium browser.")

        _PLAYWRIGHT_LAST_USED_TS = now
        return _PLAYWRIGHT_BROWSER


@asynccontextmanager
async def _shared_playwright_context(
    *,
    headless: bool,
    user_agent: str,
    locale: str = "en-US",
    viewport: dict | None = None,
    device_scale_factor: float = 1.0,
    cookies: list[dict] | None = None,
    launch_args: list[str] | None = None,
):
    browser = await _get_shared_playwright_browser(headless=headless, launch_args=launch_args)
    if browser is None:
        raise RuntimeError("playwright is not installed")

    context = await browser.new_context(
        user_agent=user_agent,
        locale=locale,
        viewport=viewport or {"width": 1280, "height": 1600},
        device_scale_factor=device_scale_factor,
    )
    if cookies:
        try:
            await context.add_cookies(cookies)
        except Exception as exc:
            add_debug_note(f"Shared browser cookie load failed: {_short_exc(exc)}")
    try:
        yield context
    finally:
        try:
            await context.close()
        finally:
            global _PLAYWRIGHT_LAST_USED_TS
            _PLAYWRIGHT_LAST_USED_TS = time.monotonic()


async def _to_thread(func, *args, **kwargs):
    to_thread = getattr(asyncio, "to_thread", None)
    if callable(to_thread):
        return await to_thread(func, *args, **kwargs)
    loop = asyncio.get_running_loop()
    if kwargs:
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))
    return await loop.run_in_executor(None, func, *args)


def _resolve_yt_dlp_cookiefile() -> str | None:
    global _YTDLP_COOKIEFILE_RESOLVED, _YTDLP_COOKIEFILE_INIT_ATTEMPTED
    if _YTDLP_COOKIEFILE_INIT_ATTEMPTED:
        return _YTDLP_COOKIEFILE_RESOLVED
    _YTDLP_COOKIEFILE_INIT_ATTEMPTED = True

    # Option 1: absolute/relative file path already present in runtime.
    cookie_path = (os.getenv("YTDLP_COOKIE_FILE") or "").strip()
    if cookie_path and Path(cookie_path).exists():
        _YTDLP_COOKIEFILE_RESOLVED = cookie_path
        add_debug_note("yt-dlp cookie file detected from YTDLP_COOKIE_FILE.")
        return _YTDLP_COOKIEFILE_RESOLVED

    # Option 2: base64-encoded Netscape cookie content.
    cookie_b64 = (os.getenv("YTDLP_COOKIES_B64") or "").strip()
    if cookie_b64:
        try:
            decoded = base64.b64decode(cookie_b64).decode("utf-8", errors="ignore").strip()
            if decoded:
                fd, tmp_path = tempfile.mkstemp(prefix="yt_dlp_cookies_", suffix=".txt")
                os.close(fd)
                Path(tmp_path).write_text(decoded + "\n")
                _YTDLP_COOKIEFILE_RESOLVED = tmp_path
                add_debug_note("yt-dlp cookie file materialized from YTDLP_COOKIES_B64.")
                return _YTDLP_COOKIEFILE_RESOLVED
        except Exception:
            add_debug_note("Failed to decode YTDLP_COOKIES_B64.")
            return None

    return None


def _resolve_po_tokens() -> dict[str, str]:
    global _YTDLP_PO_TOKENS, _YTDLP_PO_ENABLED
    if _YTDLP_PO_TOKENS is not None:
        return _YTDLP_PO_TOKENS

    if _YTDLP_PO_ENABLED is None:
        _YTDLP_PO_ENABLED = _is_truthy_env("YTDLP_ENABLE_PO_TOKENS", default=False)
    if not _YTDLP_PO_ENABLED:
        if (os.getenv("YTDLP_PO_TOKEN_WEB") or os.getenv("YTDLP_PO_TOKEN_ANDROID")):
            add_debug_note("PO tokens configured but ignored (set YTDLP_ENABLE_PO_TOKENS=1 to enable).")
        _YTDLP_PO_TOKENS = {}
        return _YTDLP_PO_TOKENS

    tokens: dict[str, str] = {}
    web = (os.getenv("YTDLP_PO_TOKEN_WEB") or "").strip()
    android = (os.getenv("YTDLP_PO_TOKEN_ANDROID") or "").strip()
    if web:
        tokens["web"] = web
    if android:
        tokens["android"] = android
    if tokens:
        add_debug_note("PO token(s) detected for YouTube clients: " + ",".join(sorted(tokens.keys())))
    _YTDLP_PO_TOKENS = tokens
    return _YTDLP_PO_TOKENS


def _with_po_tokens(opts: dict, preferred_client: str | None = None) -> dict:
    tokens = _resolve_po_tokens()
    if not tokens:
        return opts

    ex = dict(opts.get("extractor_args") or {})
    yt = dict(ex.get("youtube") or {})
    po_list: list[str] = []

    if preferred_client and preferred_client in tokens:
        po_list.append(f"{preferred_client}+{tokens[preferred_client]}")
    for client in ("web", "android"):
        if client in tokens and (not preferred_client or client != preferred_client):
            po_list.append(f"{client}+{tokens[client]}")

    if po_list:
        yt["po_token"] = po_list
        if "player_client" not in yt and preferred_client:
            yt["player_client"] = [preferred_client]
        ex["youtube"] = yt
        opts = {**opts, "extractor_args": ex}
    return opts


def _yt_dlp_retry_option_sets(base: dict) -> list[dict]:
    """Return progressively more permissive yt-dlp option sets."""
    user_agent = os.getenv(
        "YTDLP_USER_AGENT",
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        ),
    )
    common = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "geo_bypass": True,
        "socket_timeout": 20,
        "http_headers": {"User-Agent": user_agent},
    }
    cookiefile = _resolve_yt_dlp_cookiefile()
    if cookiefile:
        common["cookiefile"] = cookiefile
    sets = []
    # Standard web client.
    s1 = _with_po_tokens({**common, **base}, preferred_client="web")
    sets.append(s1)
    # Android client often works when web client is blocked.
    s2 = _with_po_tokens(
        {
        **common,
        **base,
        "extractor_args": {"youtube": {"player_client": ["android"]}},
        },
        preferred_client="android",
    )
    sets.append(s2)
    # Embedded/web fallback with retries and permissive extractors.
    s3 = _with_po_tokens(
        {
        **common,
        **base,
        "extractor_args": {"youtube": {"player_client": ["web", "web_embedded", "android"]}},
        "retries": 2,
        "fragment_retries": 2,
        },
        preferred_client="web",
    )
    sets.append(s3)
    return sets


def _yt_dlp_candidate_urls(url: str) -> list[str]:
    candidates = [url]
    video_id = extract_youtube_video_id(url)
    if video_id:
        watch_url = f"https://www.youtube.com/watch?v={video_id}"
        if watch_url not in candidates:
            candidates.append(watch_url)
    return candidates


def _yt_dlp_extract_with_retries(url: str, base_opts: dict, download: bool) -> tuple[dict, str | None]:
    def _is_hard_block(exc: Exception) -> bool:
        return _is_youtube_hard_block_text(str(exc))

    last_exc: Exception | None = None
    for candidate_url in _yt_dlp_candidate_urls(url):
        for opts in _yt_dlp_retry_option_sets(base_opts):
            try:
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(candidate_url, download=download)
                    out = ydl.prepare_filename(info) if download and info else None
                if candidate_url != url:
                    add_debug_note("yt-dlp succeeded via watch-url fallback.")
                return info or {}, out
            except Exception as exc:
                last_exc = exc
                if _is_hard_block(exc):
                    add_debug_note("yt-dlp hard-block detected; stopping retry loop early.")
                    break
                continue
        if last_exc and _is_hard_block(last_exc):
            break
    if last_exc:
        add_debug_note(f"yt-dlp retries exhausted: {_short_exc(last_exc)}")
        if _is_hard_block(last_exc):
            raise YouTubeHardBlockError(str(last_exc)) from last_exc
        raise last_exc
    return {}, None


def extract_youtube_video_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.strip("/")

    if host == "youtu.be" and path:
        return path.split("/")[0]

    if "youtube.com" in host:
        if path.startswith("shorts/"):
            return path.split("/")[1] if len(path.split("/")) > 1 else None
        if path == "watch":
            query_v = parse_qs(parsed.query).get("v")
            return query_v[0] if query_v else None

    return None


def _fetch_youtube_metadata_sync(url: str) -> dict:
    base_opts = {
        "skip_download": True,
        "extract_flat": False,
    }
    info, _ = _yt_dlp_extract_with_retries(url, base_opts, download=False)
    return info


def _parse_srt(text: str) -> str:
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if "-->" in line:
            continue
        if line.isdigit():
            continue
        lines.append(line)
    return " ".join(lines).strip()


def _load_pytube_video(url: str):
    if PytubeYouTube is None:
        return None

    po_token_web = (os.getenv("YTDLP_PO_TOKEN_WEB") or "").strip()
    visitor_data = (os.getenv("YTDLP_VISITOR_DATA") or "").strip()

    def _po_verifier():
        return visitor_data, po_token_web

    last_exc: Exception | None = None
    for candidate in _yt_dlp_candidate_urls(url):
        try:
            kwargs = {}
            if po_token_web and visitor_data:
                kwargs = {"client": "WEB", "use_po_token": True, "po_token_verifier": _po_verifier}
            return PytubeYouTube(candidate, **kwargs)
        except Exception as exc:
            last_exc = exc
            continue
    if last_exc:
        add_debug_note(f"pytubefix load failed: {_short_exc(last_exc)}")
    return None


def _fetch_youtube_metadata_pytube_sync(url: str) -> dict:
    yt = _load_pytube_video(url)
    if yt is None:
        return {}
    try:
        return {
            "title": str(getattr(yt, "title", "") or "").strip(),
            "description": str(getattr(yt, "description", "") or "").strip(),
            "uploader": str(getattr(yt, "author", "") or "").strip(),
        }
    except Exception as exc:
        add_debug_note(f"pytubefix metadata parse failed: {_short_exc(exc)}")
        return {}


async def _fetch_youtube_oembed(url: str) -> dict:
    endpoint = "https://www.youtube.com/oembed"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(endpoint, params={"url": url, "format": "json"})
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, dict) else {}
    except Exception:
        add_debug_note("YouTube oEmbed request failed.")
        return {}


async def _fetch_youtube_data_api_metadata(video_id: str) -> dict:
    api_key = (os.getenv("YOUTUBE_DATA_API_KEY") or "").strip()
    if not api_key:
        return {}

    endpoint = "https://www.googleapis.com/youtube/v3/videos"
    parts = (os.getenv("YOUTUBE_DATA_API_PARTS") or "snippet").strip() or "snippet"
    timeout_sec = _safe_int_env("YOUTUBE_DATA_API_TIMEOUT_SEC", default=15, minimum=5, maximum=60)
    params = {"part": parts, "id": video_id, "key": api_key}

    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            resp = await client.get(endpoint, params=params)
            if resp.status_code in {400, 401, 403, 404}:
                add_debug_note(f"YouTube Data API metadata unavailable (HTTP {resp.status_code}).")
                return {}
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        add_debug_note(f"YouTube Data API metadata request failed: {_short_exc(exc)}")
        return {}

    if not isinstance(data, dict):
        return {}
    items = data.get("items")
    if not isinstance(items, list) or not items:
        return {}
    item = items[0] if isinstance(items[0], dict) else {}
    snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
    statistics = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}

    tags = snippet.get("tags") if isinstance(snippet.get("tags"), list) else []
    tags = [str(t).strip() for t in tags if str(t).strip()]

    return {
        "title": str(snippet.get("title") or "").strip(),
        "description": str(snippet.get("description") or "").strip(),
        "uploader": str(snippet.get("channelTitle") or "").strip(),
        "published_at": str(snippet.get("publishedAt") or "").strip(),
        "tags": tags,
        "view_count": str(statistics.get("viewCount") or "").strip(),
    }


async def _scrape_watch_page_metadata(video_id: str) -> tuple[str, str]:
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(watch_url)
            resp.raise_for_status()
            html = resp.text
    except Exception:
        add_debug_note("Watch-page metadata scrape failed.")
        return "", ""

    soup = BeautifulSoup(html, "html.parser")
    title = ""
    desc = ""

    og_title = soup.find("meta", attrs={"property": "og:title"})
    if og_title and og_title.get("content"):
        title = og_title["content"].strip()

    og_desc = soup.find("meta", attrs={"property": "og:description"})
    if og_desc and og_desc.get("content"):
        desc = og_desc["content"].strip()

    if not title:
        t = soup.find("title")
        if t and t.text:
            title = t.text.strip()

    return title, desc


def _extract_json_after_marker(text: str, marker: str) -> dict | None:
    idx = text.find(marker)
    if idx < 0:
        return None
    start = text.find("{", idx)
    if start < 0:
        return None

    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue

        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                raw = text[start : i + 1]
                try:
                    parsed = json.loads(raw)
                except Exception:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


def _caption_track_download_candidates(tracks: list[dict]) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str, int]] = []
    for t in tracks:
        if not isinstance(t, dict):
            continue
        base_url = str(t.get("baseUrl") or "").strip()
        if not base_url:
            continue
        language = str(t.get("languageCode") or "")
        kind = str(t.get("kind") or "")
        priority = 3
        if language in {"en", "en-US", "en-GB"}:
            priority = 0 if kind != "asr" else 1
        elif kind != "asr":
            priority = 2
        candidates.append((base_url, language, priority))

    candidates.sort(key=lambda x: x[2])
    out: list[tuple[str, str]] = []
    seen = set()
    for base_url, _lang, _prio in candidates:
        if base_url in seen:
            continue
        seen.add(base_url)
        out.append((base_url + "&fmt=vtt", "vtt"))
        out.append((base_url + "&fmt=json3", "json3"))
        out.append((base_url, "vtt"))
    return out


async def _fetch_transcript_from_caption_tracks(tracks: list[dict]) -> str:
    for track_url, ext in _caption_track_download_candidates(tracks):
        text = await _fetch_text_from_subtitle_track(track_url, ext)
        if text:
            return text
    return ""


def _playwright_cookies_from_netscape_file(cookie_file: str) -> list[dict]:
    path = Path(cookie_file)
    if not path.exists():
        return []

    cookies: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []

    for line in lines:
        row = line.strip()
        if not row or row.startswith("#"):
            continue
        parts = row.split("\t")
        if len(parts) < 7:
            continue
        domain, _flag, path_value, secure_raw, expires_raw, name, value = parts[:7]
        if not name:
            continue
        secure = str(secure_raw).upper() == "TRUE"
        cookie: dict[str, object] = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": path_value or "/",
            "secure": secure,
        }
        try:
            exp = int(expires_raw)
            if exp > 0:
                cookie["expires"] = exp
        except Exception:
            pass
        cookies.append(cookie)
    return cookies


def _clean_ocr_text(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text or "").strip()
    if not normalized:
        return ""
    if len(normalized) < 8:
        return ""
    if sum(ch.isalnum() for ch in normalized) < 6:
        return ""
    return normalized


def _ocr_preprocessed_region(image: Image.Image) -> str:
    gray = image.convert("L")
    base = ImageOps.autocontrast(gray)
    variants = [
        base,
        base.point(lambda p: 255 if p > 150 else 0),
    ]
    best = ""
    for variant in variants:
        enlarged = variant.resize((int(variant.width * 1.5), int(variant.height * 1.5)))
        try:
            txt = image_to_string(enlarged, config="--oem 1 --psm 6", timeout=4)
        except Exception:
            txt = ""
        txt = _clean_ocr_text(txt)
        if len(txt) > len(best):
            best = txt
    return best


def _ocr_browser_subtitle_text(png: bytes) -> str:
    try:
        img = Image.open(io.BytesIO(png)).convert("RGB")
    except Exception:
        return ""

    w, h = img.size
    if w < 200 or h < 200:
        return ""

    # Candidate subtitle regions: center-lower and lower-third zones.
    boxes = [
        (int(0.06 * w), int(0.48 * h), int(0.94 * w), int(0.93 * h)),
        (int(0.12 * w), int(0.56 * h), int(0.88 * w), int(0.90 * h)),
    ]
    best = ""
    for x1, y1, x2, y2 in boxes:
        if x2 <= x1 or y2 <= y1:
            continue
        txt = _ocr_preprocessed_region(img.crop((x1, y1, x2, y2)))
        if len(txt) > len(best):
            best = txt
    if best:
        return best
    return _ocr_preprocessed_region(img)


async def _browser_watch_fallback(video_id: str) -> tuple[str, str, str]:
    if not _is_truthy_env("BROWSER_INGESTION_ENABLED", default=False):
        return "", "", ""
    if async_playwright is None:
        add_debug_note("Browser ingestion enabled but playwright is not installed.")
        return "", "", ""

    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    timeout_sec = _safe_int_env("BROWSER_INGESTION_TIMEOUT_SEC", default=30, minimum=10, maximum=120)
    timeout_ms = timeout_sec * 1000
    headless = _is_truthy_env("BROWSER_INGESTION_HEADLESS", default=True)
    user_agent = os.getenv(
        "BROWSER_INGESTION_USER_AGENT",
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        ),
    )

    title = ""
    desc = ""
    transcript = ""
    tracks: list[dict] = []

    cookie_file = _resolve_yt_dlp_cookiefile()
    cookies = _playwright_cookies_from_netscape_file(cookie_file) if cookie_file else []

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=headless,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = await browser.new_context(
                user_agent=user_agent,
                locale="en-US",
                viewport={"width": 960, "height": 1700},
                device_scale_factor=1.25,
            )
            if cookies:
                try:
                    await context.add_cookies(cookies)
                except Exception as exc:
                    add_debug_note(f"Browser ingestion cookie load failed: {_short_exc(exc)}")

            page = await context.new_page()
            await page.goto(watch_url, wait_until="domcontentloaded", timeout=timeout_ms)
            await page.wait_for_timeout(1200)

            payload = await page.evaluate(
                """() => {
                    const title =
                      document.querySelector('meta[property="og:title"]')?.content ||
                      document.title ||
                      '';
                    const description =
                      document.querySelector('meta[property="og:description"]')?.content || '';
                    const player = window.ytInitialPlayerResponse || null;
                    const tracks =
                      player?.captions?.playerCaptionsTracklistRenderer?.captionTracks || [];
                    return { title, description, tracks };
                }"""
            )
            if isinstance(payload, dict):
                title = str(payload.get("title") or "").strip()
                desc = str(payload.get("description") or "").strip()
                raw_tracks = payload.get("tracks")
                if isinstance(raw_tracks, list):
                    tracks = [t for t in raw_tracks if isinstance(t, dict)]

            if not tracks:
                html = await page.content()
                player = None
                for marker in ("ytInitialPlayerResponse = ", "var ytInitialPlayerResponse = ", "ytInitialPlayerResponse="):
                    player = _extract_json_after_marker(html, marker)
                    if player:
                        break
                if isinstance(player, dict):
                    captions = player.get("captions")
                    renderer = captions.get("playerCaptionsTracklistRenderer") if isinstance(captions, dict) else None
                    raw_tracks = renderer.get("captionTracks") if isinstance(renderer, dict) else None
                    if isinstance(raw_tracks, list):
                        tracks = [t for t in raw_tracks if isinstance(t, dict)]

            await context.close()
            await browser.close()
    except Exception as exc:
        add_debug_note(f"Browser ingestion fallback failed: {_short_exc(exc)}")
        return "", "", ""

    if tracks:
        transcript = await _fetch_transcript_from_caption_tracks(tracks)

    if not transcript:
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(
                    headless=headless,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                context = await browser.new_context(
                    user_agent=user_agent,
                    locale="en-US",
                    viewport={"width": 960, "height": 1700},
                    device_scale_factor=1.25,
                )
                if cookies:
                    try:
                        await context.add_cookies(cookies)
                    except Exception:
                        pass
                page = await context.new_page()
                await page.goto(watch_url, wait_until="domcontentloaded", timeout=timeout_ms)
                await page.wait_for_timeout(1000)
                try:
                    await page.evaluate(
                        """() => {
                            const v = document.querySelector('video');
                            if (v) {
                                v.muted = true;
                                const p = v.play();
                                if (p && typeof p.catch === 'function') p.catch(() => {});
                            }
                        }"""
                    )
                except Exception:
                    pass

                ocr_samples = _safe_int_env("BROWSER_OCR_SAMPLES", default=3, minimum=2, maximum=8)
                ocr_interval_ms = _safe_int_env("BROWSER_OCR_INTERVAL_MS", default=700, minimum=300, maximum=2000)
                snippets: list[str] = []
                for _ in range(ocr_samples):
                    png = await page.screenshot(full_page=False, type="png")
                    try:
                        txt = _ocr_browser_subtitle_text(png)
                    except Exception:
                        txt = ""
                    txt = _clean_ocr_text(txt)
                    if txt:
                        snippets.append(txt)
                    await page.wait_for_timeout(ocr_interval_ms)

                await context.close()
                await browser.close()

                dedup: list[str] = []
                for s in snippets:
                    if not dedup or s != dedup[-1]:
                        dedup.append(s)
                browser_ocr = " ".join(dedup).strip()
                if browser_ocr:
                    transcript = browser_ocr
                    add_debug_note("Browser fallback recovered OCR text from rendered video frames.")
        except Exception as exc:
            add_debug_note(f"Browser OCR fallback failed: {_short_exc(exc)}")

    if title or desc or transcript:
        add_debug_note("Browser-rendered watch-page fallback produced additional ingestion signals.")
    return title, desc, transcript


async def _fetch_watch_page_caption_tracks(video_id: str) -> list[tuple[str, str]]:
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(watch_url)
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:
        add_debug_note(f"Watch-page caption fetch failed: {_short_exc(exc)}")
        return []

    markers = [
        "ytInitialPlayerResponse = ",
        "var ytInitialPlayerResponse = ",
        "ytInitialPlayerResponse=",
    ]
    player = None
    for marker in markers:
        player = _extract_json_after_marker(html, marker)
        if player:
            break
    if not player:
        add_debug_note("Watch-page caption fallback: ytInitialPlayerResponse not found.")
        return []

    captions = player.get("captions") if isinstance(player, dict) else None
    renderer = captions.get("playerCaptionsTracklistRenderer") if isinstance(captions, dict) else None
    tracks = renderer.get("captionTracks") if isinstance(renderer, dict) else None
    if not isinstance(tracks, list):
        add_debug_note("Watch-page caption fallback: no caption tracks in player response.")
        return []

    return _caption_track_download_candidates([t for t in tracks if isinstance(t, dict)])


async def _fetch_transcript_from_watch_page(video_id: str) -> str:
    tracks = await _fetch_watch_page_caption_tracks(video_id)
    for url, ext in tracks:
        text = await _fetch_text_from_subtitle_track(url, ext)
        if text:
            return text
    return ""


async def _fetch_transcript_from_timedtext_api(video_id: str) -> str:
    endpoint = "https://www.youtube.com/api/timedtext"
    query_candidates = [
        {"v": video_id, "lang": "en", "fmt": "vtt"},
        {"v": video_id, "lang": "en", "fmt": "json3"},
        {"v": video_id, "lang": "en", "kind": "asr", "fmt": "vtt"},
        {"v": video_id, "lang": "en", "kind": "asr", "fmt": "json3"},
        {"v": video_id, "lang": "en"},
    ]
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            for params in query_candidates:
                try:
                    resp = await client.get(endpoint, params=params)
                    if resp.status_code >= 400:
                        continue
                    payload = resp.text
                except Exception:
                    continue

                parsed = _parse_vtt(payload) or _parse_json3(payload) or _parse_timedtext_xml(payload)
                if parsed:
                    return parsed
    except Exception as exc:
        add_debug_note(f"Timedtext API fallback failed: {_short_exc(exc)}")
    return ""


def _fetch_transcript_sync(video_id: str) -> str:
    langs = ["en", "en-US", "en-GB"]
    try:
        segments = YouTubeTranscriptApi.get_transcript(video_id, languages=langs)
    except Exception:
        segments = YouTubeTranscriptApi.get_transcript(video_id)

    return " ".join(seg.get("text", "").strip() for seg in segments if seg.get("text")).strip()


def _parse_vtt(text: str) -> str:
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(("WEBVTT", "NOTE")):
            continue
        if "-->" in line:
            continue
        if line.isdigit():
            continue
        lines.append(line)
    return " ".join(lines).strip()


def _parse_json3(text: str) -> str:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return ""
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list):
        return ""
    parts: list[str] = []
    for event in events:
        segs = event.get("segs") if isinstance(event, dict) else None
        if not isinstance(segs, list):
            continue
        for seg in segs:
            utf8 = seg.get("utf8") if isinstance(seg, dict) else None
            if isinstance(utf8, str):
                parts.append(utf8.strip())
    return " ".join(p for p in parts if p).strip()


def _parse_timedtext_xml(text: str) -> str:
    try:
        soup = BeautifulSoup(text, "xml")
    except Exception:
        return ""
    parts: list[str] = []
    for node in soup.find_all("text"):
        t = node.get_text(" ", strip=True)
        if t:
            parts.append(t)
    return " ".join(parts).strip()


def _extract_text_from_pytube_caption(caption_obj) -> str:
    if caption_obj is None:
        return ""
    for attr in ("xml_captions", "xml_caption"):
        payload = getattr(caption_obj, attr, None)
        if callable(payload):
            try:
                payload = payload()
            except Exception:
                payload = None
        if isinstance(payload, str) and payload.strip():
            text = _parse_timedtext_xml(payload)
            if text:
                return text

    for meth in ("generate_srt_captions", "generate_srt"):
        fn = getattr(caption_obj, meth, None)
        if not callable(fn):
            continue
        try:
            srt = fn()
        except Exception:
            continue
        if isinstance(srt, str) and srt.strip():
            text = _parse_srt(srt)
            if text:
                return text
    return ""


def _fetch_pytube_caption_text_sync(url: str) -> str:
    yt = _load_pytube_video(url)
    if yt is None:
        return ""

    captions = getattr(yt, "captions", None)
    if captions is None:
        return ""

    preferred = ["en", "a.en", "en-US", "a.en-US", "en-GB", "a.en-GB"]
    tried_codes: list[str] = []
    for code in preferred:
        cap = None
        try:
            if hasattr(captions, "get_by_language_code"):
                cap = captions.get_by_language_code(code)
        except Exception:
            cap = None
        if cap is None:
            try:
                cap = captions[code]
            except Exception:
                cap = None
        tried_codes.append(code)
        text = _extract_text_from_pytube_caption(cap)
        if text:
            return text

    try:
        keys = list(captions.keys()) if hasattr(captions, "keys") else []
    except Exception:
        keys = []
    for key in keys:
        if key in tried_codes:
            continue
        cap = None
        try:
            cap = captions[key]
        except Exception:
            cap = None
        text = _extract_text_from_pytube_caption(cap)
        if text:
            return text

    return ""


async def _fetch_text_from_subtitle_track(track_url: str, ext: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            res = await client.get(track_url)
            res.raise_for_status()
            payload = res.text
    except Exception:
        return ""

    ext_lower = (ext or "").lower()
    if ext_lower == "vtt":
        return _parse_vtt(payload)
    if ext_lower in {"json3", "json"}:
        return _parse_json3(payload)
    if ext_lower in {"xml", "ttml", "srv1", "srv2", "srv3"}:
        return _parse_timedtext_xml(payload)
    return _parse_vtt(payload) or _parse_json3(payload) or _parse_timedtext_xml(payload)


async def _extract_transcript_from_ydl_info(info: dict) -> str:
    def _gather_tracks(key: str) -> list[dict]:
        tracks: list[dict] = []
        block = info.get(key) or {}
        if not isinstance(block, dict):
            return tracks
        lang_priority = ["en", "en-US", "en-GB"]
        keys = lang_priority + [k for k in block.keys() if k not in lang_priority]
        for lang in keys:
            entries = block.get(lang) or []
            if isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, dict) and entry.get("url"):
                        tracks.append(entry)
        return tracks

    tracks = _gather_tracks("subtitles") + _gather_tracks("automatic_captions")
    for track in tracks:
        text = await _fetch_text_from_subtitle_track(track.get("url", ""), track.get("ext", ""))
        if text:
            return text
    return ""


def _download_audio_sync(url: str, outdir: str) -> str | None:
    def _pytube_audio_fallback() -> str | None:
        yt = _load_pytube_video(url)
        if yt is None:
            return None
        try:
            streams = yt.streams.filter(only_audio=True)
            stream = streams.order_by("abr").desc().first() if hasattr(streams, "order_by") else None
            if stream is None and hasattr(streams, "first"):
                stream = streams.first()
            if stream is None:
                return None
            out = stream.download(output_path=outdir, filename=f"{getattr(yt, 'video_id', 'yt')}_pt_audio")
            return out if out and Path(out).exists() else None
        except Exception as exc:
            add_debug_note(f"pytubefix audio fallback failed: {_short_exc(exc)}")
            return None

    base_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(Path(outdir) / "%(id)s.%(ext)s"),
    }
    try:
        info, out = _yt_dlp_extract_with_retries(url, base_opts, download=True)
    except Exception as exc:
        add_debug_note(f"yt-dlp audio download failed: {_short_exc(exc)}")
        if isinstance(exc, YouTubeHardBlockError):
            add_debug_note("Skipping pytubefix audio fallback after YouTube hard-block.")
            return None
        out = _pytube_audio_fallback()
        return out

    if (not out or not Path(out).exists()) and info:
        req = info.get("requested_downloads") if isinstance(info, dict) else None
        if isinstance(req, list) and req and isinstance(req[0], dict):
            fp = req[0].get("filepath")
            if isinstance(fp, str):
                out = fp
    if out and Path(out).exists():
        return out
    return _pytube_audio_fallback()


def _download_video_sync(url: str, outdir: str) -> str | None:
    def _pytube_video_fallback() -> str | None:
        yt = _load_pytube_video(url)
        if yt is None:
            return None
        try:
            streams = yt.streams.filter(progressive=True, file_extension="mp4")
            stream = streams.order_by("resolution").desc().first() if hasattr(streams, "order_by") else None
            if stream is None:
                streams = yt.streams.filter(file_extension="mp4")
                stream = streams.order_by("resolution").desc().first() if hasattr(streams, "order_by") else None
            if stream is None and hasattr(streams, "first"):
                stream = streams.first()
            if stream is None:
                return None
            out = stream.download(output_path=outdir, filename=f"{getattr(yt, 'video_id', 'yt')}_pt_video")
            return out if out and Path(out).exists() else None
        except Exception as exc:
            add_debug_note(f"pytubefix video fallback failed: {_short_exc(exc)}")
            return None

    base_opts = {
        "format": "mp4[height<=720]/best[height<=720]/best",
        "outtmpl": str(Path(outdir) / "%(id)s_video.%(ext)s"),
    }
    try:
        info, out = _yt_dlp_extract_with_retries(url, base_opts, download=True)
    except Exception as exc:
        add_debug_note(f"yt-dlp video download failed: {_short_exc(exc)}")
        if isinstance(exc, YouTubeHardBlockError):
            add_debug_note("Skipping pytubefix video fallback after YouTube hard-block.")
            return None
        out = _pytube_video_fallback()
        return out

    if (not out or not Path(out).exists()) and info:
        req = info.get("requested_downloads") if isinstance(info, dict) else None
        if isinstance(req, list) and req and isinstance(req[0], dict):
            fp = req[0].get("filepath")
            if isinstance(fp, str):
                out = fp
    if out and Path(out).exists():
        return out
    return _pytube_video_fallback()


def _probe_media_streams_sync(video_path: str) -> dict[str, bool]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-of",
        "json",
        video_path,
    ]
    try:
        proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
        data = json.loads(proc.stdout or "{}")
    except Exception as exc:
        add_debug_note(f"ffprobe stream probe failed: {_short_exc(exc)}")
        return {"has_video": False, "has_audio": False}

    streams = data.get("streams") if isinstance(data, dict) else None
    if not isinstance(streams, list):
        return {"has_video": False, "has_audio": False}

    has_video = any(isinstance(s, dict) and s.get("codec_type") == "video" for s in streams)
    has_audio = any(isinstance(s, dict) and s.get("codec_type") == "audio" for s in streams)
    return {"has_video": has_video, "has_audio": has_audio}


def _extract_audio_from_video_sync(video_path: str, outdir: str) -> tuple[str | None, str | None]:
    stream_info = _probe_media_streams_sync(video_path)
    if stream_info.get("has_video") and not stream_info.get("has_audio"):
        return None, "no_audio_stream"
    if not stream_info.get("has_video") and not stream_info.get("has_audio"):
        return None, "no_media_streams"

    audio_out = str(Path(outdir) / "audio_from_video.wav")
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        video_path,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        audio_out,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip().lower()
        if "output file #0 does not contain any stream" in stderr:
            return None, "no_audio_stream"
        return None, "ffmpeg_failed"
    return (audio_out if Path(audio_out).exists() else None), None


def _get_whisper_model():
    global _WHISPER_MODEL
    if WhisperModel is None:
        raise RuntimeError("faster-whisper not installed")
    if _WHISPER_MODEL is None:
        # Default to a lightweight model so API-node fallback stays viable on small CPU VMs.
        model_size = os.getenv("WHISPER_MODEL_SIZE", "tiny")
        compute_type = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
        _WHISPER_MODEL = WhisperModel(model_size, device="cpu", compute_type=compute_type)
    return _WHISPER_MODEL


def _transcription_provider() -> str:
    return (os.getenv("TRANSCRIPTION_PROVIDER", "local").strip().lower() or "local")


def _openai_transcription_enabled() -> bool:
    provider = _transcription_provider()
    if provider == "openai":
        return bool(os.getenv("OPENAI_API_KEY", "").strip())
    if provider == "auto":
        return bool(os.getenv("OPENAI_API_KEY", "").strip())
    return False


def _openai_transcription_model() -> str:
    return (os.getenv("OPENAI_TRANSCRIPTION_MODEL", "").strip() or "gpt-4o-mini-transcribe")


def _transcribe_openai_sync(media_path: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    model = _openai_transcription_model()
    base = (os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip() or "https://api.openai.com/v1").rstrip("/")
    timeout = float((os.getenv("OPENAI_TRANSCRIPTION_TIMEOUT_SEC", "60").strip() or "60"))
    prompt = (os.getenv("OPENAI_TRANSCRIPTION_PROMPT", "").strip())
    mime = mimetypes.guess_type(media_path)[0] or "application/octet-stream"
    data = {"model": model}
    if prompt:
        data["prompt"] = prompt

    headers = {
        "Authorization": f"Bearer {api_key}",
    }
    with open(media_path, "rb") as fh:
        files = {
            "file": (Path(media_path).name, fh, mime),
        }
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(f"{base}/audio/transcriptions", headers=headers, data=data, files=files)
            resp.raise_for_status()
    content_type = (resp.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        payload = resp.json()
        return str(payload.get("text", "")).strip()
    return resp.text.strip()


async def _transcribe_audio_with_provider(audio_path: str, source_label: str) -> str:
    if _openai_transcription_enabled():
        try:
            text = await _to_thread(_transcribe_openai_sync, audio_path)
            if text.strip():
                add_debug_note(
                    f"OpenAI transcription used {_openai_transcription_model()} for {source_label}."
                )
                return text.strip()
        except Exception as exc:
            add_debug_note(f"OpenAI transcription ({source_label}) error: {_short_exc(exc)}")

    text = await _to_thread(_transcribe_local_whisper_sync, audio_path)
    if text.strip():
        add_debug_note(f"Local Whisper transcription used for {source_label}.")
    return text.strip()


async def _transcribe_media_with_provider(media_path: str, source_label: str) -> str:
    if _openai_transcription_enabled():
        try:
            text = await _to_thread(_transcribe_openai_sync, media_path)
            if text.strip():
                add_debug_note(
                    f"OpenAI transcription used {_openai_transcription_model()} for {source_label}."
                )
                return text.strip()
        except Exception as exc:
            add_debug_note(f"OpenAI transcription ({source_label}) error: {_short_exc(exc)}")

    text = await _to_thread(_transcribe_local_whisper_media_sync, media_path)
    if text.strip():
        add_debug_note(f"Local Whisper transcription used for {source_label}.")
    return text.strip()


def _transcribe_local_whisper_sync(audio_path: str) -> str:
    model = _get_whisper_model()
    segments, _ = model.transcribe(audio_path, beam_size=1, vad_filter=True)
    return " ".join(s.text.strip() for s in segments if getattr(s, "text", "")).strip()


def _transcribe_local_whisper_media_sync(media_path: str) -> str:
    model = _get_whisper_model()
    segments, _ = model.transcribe(media_path, beam_size=1, vad_filter=True)
    return " ".join(s.text.strip() for s in segments if getattr(s, "text", "")).strip()


def _safe_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        value = int(raw) if raw else default
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


async def _local_asr_fallback(url: str) -> str:
    with tempfile.TemporaryDirectory() as td:
        # First attempt: direct audio extraction.
        try:
            audio_path = await _to_thread(_download_audio_sync, url, td)
            if audio_path:
                text = await _transcribe_audio_with_provider(audio_path, "downloaded audio")
                if text.strip():
                    return text.strip()
        except Exception as exc:
            add_debug_note(f"Local ASR fallback (audio path) error: {_short_exc(exc)}")

        # Second attempt: download video, extract audio via ffmpeg, then transcribe.
        try:
            video_path = await _to_thread(_download_video_sync, url, td)
            if not video_path:
                add_debug_note("Local ASR fallback: video download returned no file.")
                return ""
            audio_from_video, reason = await _to_thread(_extract_audio_from_video_sync, video_path, td)
            if reason == "no_audio_stream":
                add_debug_note("Local ASR fallback: source video has no audio stream; skipping ffmpeg extraction.")
                try:
                    text = await _transcribe_media_with_provider(video_path, "downloaded video media")
                    return text.strip()
                except Exception as exc:
                    add_debug_note(f"Local ASR fallback (direct media path) error: {_short_exc(exc)}")
                    return ""
            if reason == "no_media_streams":
                add_debug_note("Local ASR fallback: source video had no media streams.")
                return ""
            if not audio_from_video:
                add_debug_note("Local ASR fallback: ffmpeg audio extraction returned no file.")
                return ""
            text = await _transcribe_audio_with_provider(audio_from_video, "extracted audio")
            return text.strip()
        except Exception as exc:
            add_debug_note(f"Local ASR fallback (video path) error: {_short_exc(exc)}")
            return ""


async def _local_asr_from_video(video_path: str) -> str:
    try:
        with tempfile.TemporaryDirectory() as td:
            audio_path, reason = await _to_thread(_extract_audio_from_video_sync, video_path, td)
            if reason == "no_audio_stream":
                add_debug_note("Local ASR from video: source media has no audio stream; trying direct media transcription.")
                try:
                    text = await _transcribe_media_with_provider(video_path, "worker media")
                    return text.strip()
                except Exception as exc:
                    add_debug_note(f"Local ASR from video direct-media error: {_short_exc(exc)}")
                    return ""
            if reason == "no_media_streams":
                add_debug_note("Local ASR from video: source media had no audio or video streams.")
                return ""
            if not audio_path:
                add_debug_note("Local ASR from video: audio extraction returned no file.")
                return ""
            text = await _transcribe_audio_with_provider(audio_path, "worker extracted audio")
            return text.strip()
    except Exception as exc:
        add_debug_note(f"Local ASR from video error: {type(exc).__name__}.")
        return ""


def _extract_frames_sync(video_path: str, outdir: str, fps: int, max_frames: int) -> list[str]:
    stream_info = _probe_media_streams_sync(video_path)
    if not stream_info.get("has_video"):
        return []
    out_pattern = str(Path(outdir) / "frame_%03d.jpg")
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        video_path,
        "-vf",
        f"fps={fps}",
        "-frames:v",
        str(max_frames),
        out_pattern,
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    frames = sorted(str(p) for p in Path(outdir).glob("frame_*.jpg"))
    return frames


def _ocr_frames_sync(frame_paths: list[str]) -> str:
    snippets: list[str] = []
    for fp in frame_paths:
        try:
            txt = image_to_string(Image.open(fp))
        except Exception:
            continue
        txt = re.sub(r"\s+", " ", txt).strip()
        if txt and len(txt) >= 6:
            snippets.append(txt)
    # dedupe near-repeats
    dedup: list[str] = []
    for s in snippets:
        if not dedup or s != dedup[-1]:
            dedup.append(s)
    return " ".join(dedup).strip()


def _scan_frames_sync(frame_paths: list[str]) -> tuple[str, str, list[str]]:
    ocr_lines: list[str] = []
    per_frame_text_hits = 0
    ai_keyword_hits = 0
    scam_keyword_hits = 0
    low_motion_pairs = 0
    edge_vars: list[float] = []
    prev_gray: Image.Image | None = None

    ai_keywords = [
        "ai",
        "aigenerated",
        "ai generated",
        "synthetic",
        "deepfake",
        "midjourney",
        "stablediffusion",
    ]
    scam_keywords = [
        "guaranteed",
        "double your money",
        "act now",
        "limited time",
        "dm me",
        "send payment",
        "crypto giveaway",
    ]

    for fp in frame_paths:
        try:
            image = Image.open(fp).convert("RGB")
        except Exception:
            continue

        try:
            raw_text = image_to_string(image)
        except Exception:
            raw_text = ""
        text = re.sub(r"\s+", " ", raw_text).strip()
        if text:
            per_frame_text_hits += 1
            ocr_lines.append(text)
            lowered = text.lower()
            if any(k in lowered for k in ai_keywords):
                ai_keyword_hits += 1
            if any(k in lowered for k in scam_keywords):
                scam_keyword_hits += 1

        # Visual stability + sharpness heuristics per frame.
        gray = image.convert("L").resize((96, 96))
        edges = gray.filter(ImageFilter.FIND_EDGES)
        edge_var = float(ImageStat.Stat(edges).var[0]) if ImageStat.Stat(edges).var else 0.0
        edge_vars.append(edge_var)

        if prev_gray is not None:
            diff_img = ImageChops.difference(gray, prev_gray)
            mean_diff = float(ImageStat.Stat(diff_img).mean[0]) if ImageStat.Stat(diff_img).mean else 0.0
            if mean_diff < 2.0:
                low_motion_pairs += 1
        prev_gray = gray

    # Dedupe adjacent near-identical OCR snippets.
    dedup: list[str] = []
    for line in ocr_lines:
        if not dedup or line != dedup[-1]:
            dedup.append(line)
    ocr_text = " ".join(dedup).strip()

    frames_total = len(frame_paths)
    transitions = max(0, frames_total - 1)
    avg_edge_var = round(sum(edge_vars) / len(edge_vars), 2) if edge_vars else 0.0
    low_motion_ratio = (low_motion_pairs / transitions) if transitions else 0.0

    notes: list[str] = []
    notes.append(
        f"Frame-by-frame scan: analyzed {frames_total} frames; OCR text in {per_frame_text_hits}/{frames_total} frames."
    )
    notes.append(
        f"Visual stability: low-motion transitions {low_motion_pairs}/{transitions}; edge-variance avg {avg_edge_var}."
    )
    if ai_keyword_hits > 0:
        notes.append(f"Frame flag: AI-related on-screen cues detected in {ai_keyword_hits} frame(s).")
    if scam_keyword_hits > 0:
        notes.append(f"Frame flag: scam-like on-screen cues detected in {scam_keyword_hits} frame(s).")
    if low_motion_ratio >= 0.7 and frames_total >= 4:
        notes.append("Frame flag: high static-frame ratio detected (possible slideshow/synthetic pattern).")

    signal_parts: list[str] = []
    if ai_keyword_hits > 0:
        signal_parts.append("ai generated synthetic deepfake cues detected in frame text")
    if scam_keyword_hits > 0:
        signal_parts.append("scam urgency or guaranteed-return cues detected in frame text")
    if low_motion_ratio >= 0.7 and frames_total >= 4:
        signal_parts.append("high static-frame ratio")
    signal_text = f"Frame analysis signals: {', '.join(signal_parts)}." if signal_parts else ""
    return ocr_text, signal_text, notes


async def _frame_ocr_fallback(url: str) -> str:
    async def _thumbnail_ocr(video_id: str | None) -> str:
        if not video_id:
            return ""
        thumbs = _fallback_thumbnail_urls(video_id)
        for turl in thumbs:
            try:
                async with httpx.AsyncClient(timeout=12) as client:
                    res = await client.get(turl)
                    res.raise_for_status()
                img = Image.open(io.BytesIO(res.content)).convert("RGB")
                raw = image_to_string(img)
                txt = re.sub(r"\s+", " ", raw).strip()
                if len(txt) >= 8:
                    add_debug_note("Frame OCR fallback used thumbnail OCR recovery.")
                    return txt
            except Exception:
                continue
        return ""

    try:
        with tempfile.TemporaryDirectory() as td:
            video_path = await _to_thread(_download_video_sync, url, td)
            fps = _safe_int_env("OCR_FRAME_FPS", default=1, minimum=1, maximum=3)
            max_frames = _safe_int_env("OCR_MAX_FRAMES", default=10, minimum=3, maximum=20)
            if not video_path:
                add_debug_note("Frame OCR fallback: video download returned no file.")
                return await _thumbnail_ocr(extract_youtube_video_id(url))
            frames_dir = str(Path(td) / "frames")
            Path(frames_dir).mkdir(parents=True, exist_ok=True)
            frame_paths = await _to_thread(_extract_frames_sync, video_path, frames_dir, fps, max_frames)
            if not frame_paths:
                add_debug_note("Frame OCR fallback: no frames extracted.")
                return await _thumbnail_ocr(extract_youtube_video_id(url))
            ocr_text = await _to_thread(_ocr_frames_sync, frame_paths)
            if ocr_text:
                return ocr_text
            return await _thumbnail_ocr(extract_youtube_video_id(url))
    except Exception as exc:
        add_debug_note(f"Frame OCR fallback error: {_short_exc(exc)}")
        return await _thumbnail_ocr(extract_youtube_video_id(url))


async def _scan_frames_from_video(video_path: str) -> tuple[str, str, list[str]]:
    try:
        with tempfile.TemporaryDirectory() as td:
            fps = _safe_int_env("OCR_FRAME_FPS", default=1, minimum=1, maximum=3)
            max_frames = _safe_int_env("OCR_MAX_FRAMES", default=10, minimum=3, maximum=20)
            frames_dir = str(Path(td) / "frames")
            Path(frames_dir).mkdir(parents=True, exist_ok=True)
            frame_paths = await _to_thread(_extract_frames_sync, video_path, frames_dir, fps, max_frames)
            if not frame_paths:
                add_debug_note("Frame scan from video: no frames extracted.")
                return "", "", []
            return await _to_thread(_scan_frames_sync, frame_paths)
    except Exception as exc:
        add_debug_note(f"Frame scan from video error: {type(exc).__name__}.")
        return "", "", []


def _source_alignment_note(caption: str, transcript: str) -> str:
    combined = " ".join([caption.strip(), transcript.strip()]).strip()
    if len(tokenize(combined)) < 15:
        return "Source-check: skipped (insufficient extracted text)."

    signals = extract_signals(caption, transcript)
    claims = signals.claims[:3]
    if not claims:
        return "Source-check: no check-worthy claims extracted."

    matched = 0
    total = len(claims)
    top_sources: list[str] = []
    for claim in claims:
        chunks = retrieve_evidence(claim, top_k=2)
        if not chunks:
            continue
        claim_tokens = set(tokenize(claim))
        best = 0.0
        best_src = ""
        for chunk in chunks:
            ev_tokens = set(tokenize(f"{chunk.title} {chunk.text}"))
            if not claim_tokens:
                continue
            overlap = len(claim_tokens.intersection(ev_tokens)) / len(claim_tokens)
            if overlap > best:
                best = overlap
                best_src = chunk.source_url
        if best >= 0.2:
            matched += 1
            if best_src and best_src not in top_sources:
                top_sources.append(best_src)

    if top_sources:
        refs = ", ".join(top_sources[:3])
        return f"Source-check: matched trusted evidence for {matched}/{total} claim(s). Top sources: {refs}."
    return f"Source-check: low alignment with trusted sources ({matched}/{total} claims matched)."


def _fallback_thumbnail_urls(video_id: str) -> list[str]:
    return [
        f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
        f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
    ]


async def _thumbnail_metadata_summary(thumbnail_urls: list[str]) -> str:
    found: list[str] = []
    for url in thumbnail_urls[:3]:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                res = await client.head(url)
            if res.status_code < 400:
                found.append(url.split("/")[-1])
        except Exception:
            continue

    if not found:
        return ""
    return f"Available thumbnail variants: {', '.join(found)}."


def _evidence_level_from_tokens(total_tokens: int, transcript_tokens: int, ocr_tokens: int) -> str:
    text_signal = transcript_tokens + ocr_tokens
    if total_tokens >= 120 or text_signal >= 80:
        return "high"
    if total_tokens >= 40 or text_signal >= 20:
        return "medium"
    return "low"


async def enrich_from_youtube(
    url: str, worker_mode: bool = False, include_evidence: bool = False
) -> tuple[str, str, list[str]] | tuple[str, str, list[str], dict]:
    notes: list[str] = []
    caption = ""
    transcript = ""
    ocr_fragments: list[str] = []
    asr_fragments: list[str] = []
    heavy_ingestion_enabled = _allow_heavy_ingestion(worker_mode)
    hard_blocked = False

    video_id = extract_youtube_video_id(url)
    if not video_id:
        notes.append("Could not parse YouTube video ID from URL.")
        if include_evidence:
            return caption, transcript, notes, {
                "total_tokens": 0,
                "caption_tokens": 0,
                "transcript_tokens": 0,
                "ocr_tokens": 0,
                "asr_tokens": 0,
                "level": "low",
                "transcript_present": False,
                "ocr_present": False,
                "asr_present": False,
            }
        return caption, transcript, notes

    info: dict = {}
    try:
        info = await _to_thread(_fetch_youtube_metadata_sync, url)
        title = (info.get("title") or "").strip()
        description = (info.get("description") or "").strip()
        channel = (info.get("uploader") or "").strip()

        caption_parts = [p for p in [title, description] if p]
        caption = "\n\n".join(caption_parts).strip()

        if channel:
            notes.append(f"Auto-ingested YouTube metadata from channel: {channel}.")
        else:
            notes.append("Auto-ingested YouTube metadata.")
    except Exception as exc:
        if isinstance(exc, YouTubeHardBlockError):
            hard_blocked = True
            notes.append("YouTube blocked richer extraction with a sign-in / bot check; using lighter fallback analysis.")
            add_debug_note("YouTube hard-block triggered degraded metadata-first mode.")
        else:
            notes.append("Could not fetch YouTube metadata via yt-dlp.")
        add_debug_note("yt-dlp metadata extraction failed.")
        if not hard_blocked:
            pt_meta = await _to_thread(_fetch_youtube_metadata_pytube_sync, url)
            if pt_meta:
                title = (pt_meta.get("title") or "").strip()
                description = (pt_meta.get("description") or "").strip()
                channel = (pt_meta.get("uploader") or "").strip()
                caption_parts = [p for p in [title, description] if p]
                caption = "\n\n".join(caption_parts).strip()
                if caption:
                    if channel:
                        notes.append(f"Recovered metadata via pytubefix fallback (author: {channel}).")
                    else:
                        notes.append("Recovered metadata via pytubefix fallback.")

    if worker_mode and not hard_blocked:
        try:
            with tempfile.TemporaryDirectory() as td:
                video_path = await _to_thread(_download_video_sync, url, td)
                if video_path:
                    notes.append("Worker downloaded source video for local AI analysis.")
                    if not transcript:
                        from_video = await _local_asr_from_video(video_path)
                        if from_video:
                            transcript = from_video
                            asr_fragments.append(from_video)
                            notes.append("Recovered transcript with local Whisper ASR from downloaded video.")

                    ocr_text, signal_text, frame_notes = await _scan_frames_from_video(video_path)
                    if ocr_text:
                        transcript = f"{transcript}\n\n{ocr_text}".strip() if transcript else ocr_text
                        ocr_fragments.append(ocr_text)
                        notes.append("Recovered on-screen text via frame-by-frame OCR scan.")
                    if signal_text:
                        transcript = f"{transcript}\n\n{signal_text}".strip() if transcript else signal_text
                    notes.extend(frame_notes[:4])
                else:
                    notes.append("Worker video download returned no file; using fallback ingestion.")
        except Exception as exc:
            add_debug_note(f"Worker video pipeline error: {type(exc).__name__}.")
    elif worker_mode and hard_blocked:
        notes.append("Skipped worker video download because YouTube richer extraction is bot-blocked right now.")

    if not caption:
        data_api_meta = await _fetch_youtube_data_api_metadata(video_id)
        if data_api_meta:
            title = (data_api_meta.get("title") or "").strip()
            description = (data_api_meta.get("description") or "").strip()
            channel = (data_api_meta.get("uploader") or "").strip()
            tags = data_api_meta.get("tags") if isinstance(data_api_meta.get("tags"), list) else []
            published_at = (data_api_meta.get("published_at") or "").strip()

            caption_parts = [p for p in [title, description] if p]
            if tags:
                caption_parts.append("Tags: " + ", ".join(tags[:12]))
            if published_at:
                caption_parts.append(f"Published: {published_at}")
            caption = "\n\n".join(caption_parts).strip()

            if caption:
                if channel:
                    notes.append(f"Recovered metadata from YouTube Data API (channel: {channel}).")
                else:
                    notes.append("Recovered metadata from YouTube Data API.")

    if not caption:
        oembed = await _fetch_youtube_oembed(url)
        if oembed:
            title = (oembed.get("title") or "").strip()
            author = (oembed.get("author_name") or "").strip()
            if title:
                caption = title
            if author:
                notes.append(f"Recovered metadata from YouTube oEmbed (author: {author}).")
            else:
                notes.append("Recovered metadata from YouTube oEmbed.")
        else:
            notes.append("Could not fetch YouTube metadata via oEmbed fallback.")

    if not caption:
        scraped_title, scraped_desc = await _scrape_watch_page_metadata(video_id)
        if scraped_title or scraped_desc:
            caption = "\n\n".join([p for p in [scraped_title, scraped_desc] if p]).strip()
            notes.append("Recovered metadata from YouTube watch-page scraping fallback.")

    try:
        transcript = await _to_thread(_fetch_transcript_sync, video_id)
        if transcript:
            notes.append("Auto-ingested YouTube transcript.")
        else:
            notes.append("YouTube transcript lookup returned empty text.")
    except Exception:
        notes.append("Could not fetch YouTube transcript (disabled/unavailable).")
        add_debug_note("youtube-transcript-api failed for this video.")

    if not transcript and info:
        transcript = await _extract_transcript_from_ydl_info(info)
        if transcript:
            notes.append("Recovered transcript from YouTube subtitle tracks (yt-dlp fallback).")
    if not transcript:
        pt_caption = await _to_thread(_fetch_pytube_caption_text_sync, url)
        if pt_caption:
            transcript = pt_caption
            notes.append("Recovered transcript from pytubefix caption fallback.")
    if not transcript:
        watch_page_transcript = await _fetch_transcript_from_watch_page(video_id)
        if watch_page_transcript:
            transcript = watch_page_transcript
            notes.append("Recovered transcript from YouTube watch-page caption-track fallback.")
    if not transcript:
        timedtext = await _fetch_transcript_from_timedtext_api(video_id)
        if timedtext:
            transcript = timedtext
            notes.append("Recovered transcript from YouTube timedtext endpoint fallback.")

    if heavy_ingestion_enabled and (not caption or not transcript) and not hard_blocked:
        b_title, b_desc, b_transcript = await _browser_watch_fallback(video_id)
        if not caption and (b_title or b_desc):
            caption = "\n\n".join([p for p in [b_title, b_desc] if p]).strip()
            if caption:
                notes.append("Recovered metadata from browser-rendered watch-page fallback.")
        if not transcript and b_transcript:
            transcript = b_transcript
            notes.append("Recovered transcript from browser-rendered watch-page fallback.")
    elif heavy_ingestion_enabled and hard_blocked:
        notes.append("Skipped browser-rendered YouTube fallback because richer extraction is currently bot-blocked.")

    if heavy_ingestion_enabled and not transcript and not hard_blocked:
        local_asr = await _local_asr_fallback(url)
        if local_asr:
            transcript = local_asr
            asr_fragments.append(local_asr)
            notes.append("Recovered transcript with local Whisper ASR fallback.")
    elif heavy_ingestion_enabled and hard_blocked and not transcript:
        notes.append("Skipped local YouTube ASR fallback because download paths are currently bot-blocked.")

    # Only run the lighter OCR fallback when transcript recovery still failed.
    if heavy_ingestion_enabled and not transcript and not hard_blocked:
        ocr_text = await _frame_ocr_fallback(url)
        if ocr_text:
            transcript = f"{transcript}\n\n{ocr_text}".strip() if transcript else ocr_text
            ocr_fragments.append(ocr_text)
            notes.append("Recovered on-screen text using frame OCR fallback.")
    elif heavy_ingestion_enabled and hard_blocked and not transcript:
        notes.append("Skipped YouTube OCR fallback because richer extraction is currently bot-blocked.")
    elif not heavy_ingestion_enabled:
        notes.append("Skipped heavy ASR/OCR/browser ingestion on API node to keep memory usage stable.")

    thumbnails = info.get("thumbnails") if isinstance(info, dict) else None
    thumb_urls: list[str] = []
    if isinstance(thumbnails, list) and thumbnails:
        thumb_urls = [t.get("url", "") for t in thumbnails if isinstance(t, dict) and t.get("url")]
    if not thumb_urls:
        thumb_urls = _fallback_thumbnail_urls(video_id)
        notes.append("Using deterministic YouTube thumbnail URL fallback for visual metadata checks.")

    visual_summary = await _thumbnail_metadata_summary(thumb_urls)
    if visual_summary:
        caption = f"{caption}\n\nVisual metadata: {visual_summary}".strip()
        notes.append("Added thumbnail metadata signals (open-source fallback).")

    if worker_mode:
        notes.append(_source_alignment_note(caption, transcript))

    if include_evidence:
        caption_tokens = len(tokenize(caption))
        transcript_tokens = len(tokenize(transcript))
        ocr_tokens = len(tokenize("\n".join(ocr_fragments)))
        asr_tokens = len(tokenize("\n".join(asr_fragments)))
        total_tokens = len(tokenize(f"{caption}\n{transcript}"))
        evidence = {
            "total_tokens": int(total_tokens),
            "caption_tokens": int(caption_tokens),
            "transcript_tokens": int(transcript_tokens),
            "ocr_tokens": int(ocr_tokens),
            "asr_tokens": int(asr_tokens),
            "level": _evidence_level_from_tokens(total_tokens, transcript_tokens, ocr_tokens),
            "transcript_present": bool(transcript.strip()),
            "ocr_present": ocr_tokens > 0,
            "asr_present": asr_tokens > 0,
        }
        return caption, transcript, notes, evidence

    return caption, transcript, notes
