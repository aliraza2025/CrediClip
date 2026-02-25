import asyncio
from urllib.parse import parse_qs, urlparse

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


async def enrich_from_youtube(url: str) -> tuple[str, str, list[str]]:
    notes: list[str] = []
    caption = ""
    transcript = ""

    video_id = extract_youtube_video_id(url)
    if not video_id:
        notes.append("Could not parse YouTube video ID from URL.")
        return caption, transcript, notes

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

    return caption, transcript, notes
