import asyncio
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup
from faster_whisper import WhisperModel
from PIL import Image
from pytesseract import image_to_string
from yt_dlp import YoutubeDL
from youtube_transcript_api import YouTubeTranscriptApi

from app.services.debug_state import add_debug_note

_WHISPER_MODEL: WhisperModel | None = None


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
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False,
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return info or {}


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
    return _parse_vtt(payload) or _parse_json3(payload)


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
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
        "outtmpl": str(Path(outdir) / "%(id)s.%(ext)s"),
        "noplaylist": True,
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        out = ydl.prepare_filename(info)
    return out if out and Path(out).exists() else None


def _download_video_sync(url: str, outdir: str) -> str | None:
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "mp4[height<=720]/best[height<=720]/best",
        "outtmpl": str(Path(outdir) / "%(id)s_video.%(ext)s"),
        "noplaylist": True,
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        out = ydl.prepare_filename(info)
    return out if out and Path(out).exists() else None


def _get_whisper_model() -> WhisperModel:
    global _WHISPER_MODEL
    if _WHISPER_MODEL is None:
        model_size = os.getenv("WHISPER_MODEL_SIZE", "small")
        compute_type = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
        _WHISPER_MODEL = WhisperModel(model_size, device="cpu", compute_type=compute_type)
    return _WHISPER_MODEL


def _transcribe_local_whisper_sync(audio_path: str) -> str:
    model = _get_whisper_model()
    segments, _ = model.transcribe(audio_path, beam_size=1, vad_filter=True)
    return " ".join(s.text.strip() for s in segments if getattr(s, "text", "")).strip()


async def _local_asr_fallback(url: str) -> str:
    try:
        with tempfile.TemporaryDirectory() as td:
            audio_path = await asyncio.to_thread(_download_audio_sync, url, td)
            if not audio_path:
                add_debug_note("Local ASR fallback: audio download returned no file.")
                return ""
            text = await asyncio.to_thread(_transcribe_local_whisper_sync, audio_path)
            return text.strip()
    except Exception as exc:
        add_debug_note(f"Local ASR fallback error: {type(exc).__name__}.")
        return ""


def _extract_frames_sync(video_path: str, outdir: str, fps: int, max_frames: int) -> list[str]:
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
    subprocess.run(cmd, check=True)
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


async def _frame_ocr_fallback(url: str) -> str:
    try:
        with tempfile.TemporaryDirectory() as td:
            video_path = await asyncio.to_thread(_download_video_sync, url, td)
            if not video_path:
                add_debug_note("Frame OCR fallback: video download returned no file.")
                return ""
            fps = int(os.getenv("OCR_FRAME_FPS", "1"))
            max_frames = int(os.getenv("OCR_MAX_FRAMES", "8"))
            frames_dir = str(Path(td) / "frames")
            Path(frames_dir).mkdir(parents=True, exist_ok=True)
            frame_paths = await asyncio.to_thread(_extract_frames_sync, video_path, frames_dir, fps, max_frames)
            if not frame_paths:
                add_debug_note("Frame OCR fallback: no frames extracted.")
                return ""
            return await asyncio.to_thread(_ocr_frames_sync, frame_paths)
    except Exception as exc:
        add_debug_note(f"Frame OCR fallback error: {type(exc).__name__}.")
        return ""


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


async def enrich_from_youtube(url: str) -> tuple[str, str, list[str]]:
    notes: list[str] = []
    caption = ""
    transcript = ""

    video_id = extract_youtube_video_id(url)
    if not video_id:
        notes.append("Could not parse YouTube video ID from URL.")
        return caption, transcript, notes

    info: dict = {}
    try:
        info = await asyncio.to_thread(_fetch_youtube_metadata_sync, url)
        title = (info.get("title") or "").strip()
        description = (info.get("description") or "").strip()
        channel = (info.get("uploader") or "").strip()

        caption_parts = [p for p in [title, description] if p]
        caption = "\n\n".join(caption_parts).strip()

        if channel:
            notes.append(f"Auto-ingested YouTube metadata from channel: {channel}.")
        else:
            notes.append("Auto-ingested YouTube metadata.")
    except Exception:
        notes.append("Could not fetch YouTube metadata via yt-dlp.")
        add_debug_note("yt-dlp metadata extraction failed.")

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
        transcript = await asyncio.to_thread(_fetch_transcript_sync, video_id)
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
        local_asr = await _local_asr_fallback(url)
        if local_asr:
            transcript = local_asr
            notes.append("Recovered transcript with local Whisper ASR fallback.")

    ocr_text = await _frame_ocr_fallback(url)
    if ocr_text:
        transcript = f"{transcript}\n\n{ocr_text}".strip() if transcript else ocr_text
        notes.append("Recovered on-screen text using frame OCR fallback.")

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

    return caption, transcript, notes
