#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _to_channel_url(raw: str) -> str:
    s = raw.strip()
    if s.startswith("http://") or s.startswith("https://"):
        return s
    if s.startswith("@"):
        return f"https://www.youtube.com/{s}/shorts"
    if s.startswith("channel/") or s.startswith("c/") or s.startswith("user/"):
        return f"https://www.youtube.com/{s}/shorts"
    return f"https://www.youtube.com/{s}/shorts"


def fetch_ids(channel: str, max_per_channel: int, timeout: int) -> tuple[list[str], str | None]:
    target = _to_channel_url(channel)
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--dump-json",
        "--playlist-end",
        str(max_per_channel),
        target,
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        err = (p.stderr or p.stdout or "").strip().splitlines()
        return [], (err[-1] if err else f"yt-dlp failed for {target}")

    ids: list[str] = []
    for line in (p.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        vid = str(obj.get("id") or "").strip()
        if VIDEO_ID_RE.fullmatch(vid):
            ids.append(vid)
    return ids, None


def main() -> None:
    parser = argparse.ArgumentParser(description="Build human-generated Shorts labels from public YouTube channels")
    parser.add_argument("--channels-file", default="app/data/human_channels.txt")
    parser.add_argument("--max-per-channel", type=int, default=40)
    parser.add_argument("--timeout-sec", type=int, default=90)
    parser.add_argument("--output", default="app/data/human_generation_labels.json")
    args = parser.parse_args()

    channels_path = Path(args.channels_file)
    if not channels_path.exists():
        raise FileNotFoundError(f"channels file not found: {channels_path}")

    raw_lines = [ln.strip() for ln in channels_path.read_text().splitlines()]
    channels = [ln for ln in raw_lines if ln and not ln.startswith("#")]
    if not channels:
        raise ValueError(f"No channels found in {channels_path}")

    labels: dict[str, str] = {}
    failures: list[str] = []

    for ch in channels:
        ids, err = fetch_ids(ch, max_per_channel=args.max_per_channel, timeout=args.timeout_sec)
        if err:
            failures.append(f"{ch}: {err}")
            continue
        for vid in ids:
            labels[vid] = "human_generated"

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(labels, indent=2, sort_keys=True) + "\n")

    print(f"Saved human labels: {out}")
    print(f"Channels attempted: {len(channels)}")
    print(f"Human video IDs: {len(labels)}")
    if failures:
        print(f"Channel failures: {len(failures)}")
        for f in failures[:10]:
            print(f"- {f}")


if __name__ == "__main__":
    main()
