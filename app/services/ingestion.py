import asyncio
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from yt_dlp import YoutubeDL
from youtube_transcript_api import YouTubeTranscriptApi
from app.services.debug_state import add_debug_note


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


def _download_audio_for_transcription_sync(url: str, outdir: str) -> str | None:
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
        "outtmpl": str(Path(outdir) / "%(id)s.%(ext)s"),
        "noplaylist": True,
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        downloaded = ydl.prepare_filename(info)
    return downloaded if downloaded and Path(downloaded).exists() else None


async def _openai_transcribe_file(audio_path: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        add_debug_note("Whisper skipped: OPENAI_API_KEY missing.")
        return ""

    model = os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            with open(audio_path, "rb") as fh:
                files = {
                    "file": (Path(audio_path).name, fh, "application/octet-stream"),
                    "model": (None, model),
                }
                resp = await client.post("https://api.openai.com/v1/audio/transcriptions", headers=headers, files=files)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        add_debug_note(f"Whisper API HTTP error: {exc.response.status_code}.")
        return ""
    except httpx.HTTPError as exc:
        add_debug_note(f"Whisper API network error: {type(exc).__name__}.")
        return ""
    except Exception as exc:
        add_debug_note(f"Whisper API unexpected error: {type(exc).__name__}.")
        return ""

    text = data.get("text") if isinstance(data, dict) else ""
    return text.strip() if isinstance(text, str) else ""


async def _whisper_fallback_transcript(url: str) -> str:
    try:
        with tempfile.TemporaryDirectory() as td:
            audio_path = await asyncio.to_thread(_download_audio_for_transcription_sync, url, td)
            if not audio_path:
                add_debug_note("Whisper fallback: audio download returned no file.")
                return ""
            return await _openai_transcribe_file(audio_path)
    except Exception as exc:
        add_debug_note(f"Whisper fallback exception: {type(exc).__name__}.")
        return ""


def _fallback_thumbnail_urls(video_id: str) -> list[str]:
    return [
        f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
        f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
    ]


async def _openai_thumbnail_analysis(thumbnail_urls: list[str]) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or not thumbnail_urls:
        if not api_key:
            add_debug_note("Vision skipped: OPENAI_API_KEY missing.")
        return ""

    model = os.getenv("OPENAI_VISION_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    content_items: list[dict] = [
        {
            "type": "text",
            "text": (
                "Analyze these social-video thumbnails for deception/scam/manipulation cues. "
                "Return concise plain text summary in max 3 sentences."
            ),
        }
    ]
    for url in thumbnail_urls[:2]:
        content_items.append({"type": "image_url", "image_url": {"url": url}})

    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": "You are a concise visual risk analyst."},
            {"role": "user", "content": content_items},
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
            return text.strip() if isinstance(text, str) else ""
    except httpx.HTTPStatusError as exc:
        add_debug_note(f"Vision API HTTP error: {exc.response.status_code}.")
        return ""
    except httpx.HTTPError as exc:
        add_debug_note(f"Vision API network error: {type(exc).__name__}.")
        return ""
    except Exception as exc:
        add_debug_note(f"Vision API unexpected error: {type(exc).__name__}.")
        return ""


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
        whisper_text = await _whisper_fallback_transcript(url)
        if whisper_text:
            transcript = whisper_text
            notes.append("Recovered transcript from audio using OpenAI Whisper transcription fallback.")
        else:
            notes.append("Whisper transcription fallback could not recover transcript.")

    thumbnails = info.get("thumbnails") if isinstance(info, dict) else None
    thumb_urls: list[str] = []
    if isinstance(thumbnails, list) and thumbnails:
        thumb_urls = [t.get("url", "") for t in thumbnails if isinstance(t, dict) and t.get("url")]
    if not thumb_urls:
        thumb_urls = _fallback_thumbnail_urls(video_id)
        notes.append("Using deterministic YouTube thumbnail URL fallback for visual analysis.")

    visual_summary = await _openai_thumbnail_analysis(thumb_urls)
    if visual_summary:
        if caption:
            caption = f"{caption}\n\nVisual signals: {visual_summary}"
        else:
            caption = f"Visual signals: {visual_summary}"
        notes.append("Added thumbnail-based visual risk analysis (OpenAI vision).")
    else:
        notes.append("Visual analysis fallback did not return additional signals.")

    return caption, transcript, notes
