#!/usr/bin/env python3
"""Build generation-origin labels from Kaggle AI Shorts dataset.

Source dataset:
  aibuttonfoundation/youtube-ai-slop-shorts-dataset

Output:
  app/data/generation_labels.json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd


def extract_video_id(url: str) -> str | None:
    if not isinstance(url, str) or not url.strip():
        return None
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower()
    path = parsed.path.strip("/")

    if host == "youtu.be" and path:
        return path.split("/")[0]

    if "youtube.com" in host:
        if path.startswith("shorts/"):
            parts = path.split("/")
            if len(parts) > 1 and parts[1]:
                return parts[1]
        if path == "watch":
            vals = parse_qs(parsed.query).get("v")
            if vals:
                return vals[0]
    return None


def is_shorts_url(url: str) -> bool:
    if not isinstance(url, str):
        return False
    lower = url.lower()
    return "youtube.com/shorts/" in lower or "youtu.be/" in lower


def _pick_url_column(df: pd.DataFrame) -> str:
    candidates = [
        c
        for c in df.columns
        if re.search(r"(url|link|video)", str(c), flags=re.IGNORECASE)
    ]
    if not candidates:
        raise ValueError("Could not detect a URL/video column in dataset.")

    best = None
    best_score = -1
    for col in candidates:
        values = df[col].astype(str).head(200)
        score = sum("youtube" in v.lower() or "youtu.be" in v.lower() for v in values)
        if score > best_score:
            best_score = score
            best = col
    return str(best)


def _pick_label_column(df: pd.DataFrame) -> str:
    candidates = [
        c
        for c in df.columns
        if re.search(r"(label|class|ai|generated|origin|type)", str(c), flags=re.IGNORECASE)
    ]
    if not candidates:
        raise ValueError("Could not detect an AI label column in dataset.")

    # prefer columns explicitly mentioning ai/generated
    scored = sorted(
        candidates,
        key=lambda c: (
            2 if re.search(r"(ai|generated)", str(c), flags=re.IGNORECASE) else 0,
            len(str(c)),
        ),
        reverse=True,
    )
    return str(scored[0])


def _ai_truthy(value: object) -> bool:
    if value is None:
        return False
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "y", "ai", "ai_generated", "generated", "synthetic", "fake"}:
        return True
    # catch labels like "AI Generated", "synthetic video"
    return bool(re.search(r"\b(ai|synthetic|generated|fake|deepfake)\b", s))


def load_dataset(dataset: str, file_path: str) -> pd.DataFrame:
    import kagglehub
    from kagglehub import KaggleDatasetAdapter

    return kagglehub.load_dataset(
        KaggleDatasetAdapter.PANDAS,
        dataset,
        file_path,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Train generation labels from Kaggle AI Shorts dataset")
    parser.add_argument(
        "--dataset",
        default="aibuttonfoundation/youtube-ai-slop-shorts-dataset",
        help="Kaggle dataset path",
    )
    parser.add_argument(
        "--file-path",
        default="",
        help="Optional file path within dataset (if dataset has multiple files)",
    )
    parser.add_argument(
        "--output",
        default="app/data/generation_labels.json",
        help="Output JSON label map path",
    )
    args = parser.parse_args()

    df = load_dataset(args.dataset, args.file_path)
    if df is None or df.empty:
        raise ValueError("Loaded dataset is empty.")

    url_col = _pick_url_column(df)
    label_col = _pick_label_column(df)

    rows = df[[url_col, label_col]].dropna()
    rows = rows[rows[url_col].astype(str).map(is_shorts_url)]
    rows = rows[rows[label_col].map(_ai_truthy)]

    labels: dict[str, str] = {}
    for url in rows[url_col].astype(str):
        vid = extract_video_id(url)
        if vid:
            labels[vid] = "ai_generated"

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(labels, indent=2, sort_keys=True) + "\n")

    print(f"URL column: {url_col}")
    print(f"Label column: {label_col}")
    print(f"Saved {len(labels)} AI-generated shorts labels to {out}")


if __name__ == "__main__":
    main()
