import asyncio
import json
from urllib.parse import parse_qs, urlparse

import httpx
from yt_dlp import YoutubeDL
from youtube_transcript_api import YouTubeTranscriptApi


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


def _fetch_transcript_sync(video_id: str) -> str:
    # Try common English variants first, then default language fallback.
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
        # Prefer English tracks first.
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

        if info.get("thumbnails"):
            notes.append("Thumbnail metadata detected. Frame-by-frame visual analysis is not enabled in this v1.")
    except Exception:
        notes.append("Could not fetch YouTube metadata via yt-dlp.")

    try:
        transcript = await asyncio.to_thread(_fetch_transcript_sync, video_id)
        if transcript:
            notes.append("Auto-ingested YouTube transcript.")
        else:
            notes.append("YouTube transcript lookup returned empty text.")
    except Exception:
        notes.append("Could not fetch YouTube transcript (disabled/unavailable).")

    if not transcript and info:
        transcript = await _extract_transcript_from_ydl_info(info)
        if transcript:
            notes.append("Recovered transcript from YouTube subtitle tracks (yt-dlp fallback).")

    return caption, transcript, notes
