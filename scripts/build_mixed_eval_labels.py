#!/usr/bin/env python3
"""Build mixed evaluation labels from two Kaggle datasets.

AI dataset (positive class):
  aibuttonfoundation/youtube-ai-slop-shorts-dataset

Human dataset (negative class):
  prince7489/youtube-shorts-performance-dataset

Output:
  app/data/eval_mixed_labels.json
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


def is_shorts_like_url(url: str) -> bool:
    if not isinstance(url, str):
        return False
    lower = url.lower()
    return "youtube.com/shorts/" in lower or "youtu.be/" in lower


VIDEO_ID_RE = re.compile(r"\b[A-Za-z0-9_-]{11}\b")
VIDEO_ID_LOOSE_RE = re.compile(r"^[A-Za-z0-9_-]{6,20}$")


def extract_video_id_from_text(value: object) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None

    # URL path first.
    vid = extract_video_id(s)
    if vid:
        return vid

    # Raw YouTube ID fallback.
    if VIDEO_ID_RE.fullmatch(s):
        return s

    # Embedded ID in arbitrary text.
    m = VIDEO_ID_RE.search(s)
    if m:
        return m.group(0)

    # Loose fallback for datasets that store ids not strictly parsed as URLs.
    token = s.split()[0].strip().strip(",.;:()[]{}<>\"'")
    if VIDEO_ID_LOOSE_RE.fullmatch(token):
        return token
    return None


def is_strict_youtube_id(value: str) -> bool:
    return bool(VIDEO_ID_RE.fullmatch(value or ""))


def pick_url_column(df: pd.DataFrame) -> str:
    candidates = [c for c in df.columns if re.search(r"(url|link|video)", str(c), flags=re.IGNORECASE)]
    if not candidates:
        raise ValueError("Could not detect URL/video column.")

    best_col = None
    best_score = -1
    for col in candidates:
        vals = df[col].astype(str).head(400)
        score = sum(("youtube.com" in v.lower() or "youtu.be" in v.lower()) for v in vals)
        if score > best_score:
            best_score = score
            best_col = str(col)
    return str(best_col)


def load_dataset(dataset: str, file_path: str) -> pd.DataFrame:
    import kagglehub
    from kagglehub import KaggleDatasetAdapter

    if file_path:
        return kagglehub.load_dataset(
            KaggleDatasetAdapter.PANDAS,
            dataset,
            file_path,
        )

    # Auto-detect a loadable dataset file when path is not provided.
    root = Path(kagglehub.dataset_download(dataset))
    candidates = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in {
        ".csv", ".tsv", ".json", ".jsonl", ".xml", ".parquet", ".feather",
        ".sqlite", ".sqlite3", ".db", ".db3", ".s3db", ".dl3",
        ".xls", ".xlsx", ".xlsm", ".xlsb", ".odf", ".ods", ".odt",
    }]
    if not candidates:
        raise ValueError(f"No supported data files found under dataset download: {root}")

    # Prefer JSON/CSV-ish files with shorts/youtube hints in the filename.
    def _rank(path: Path) -> tuple[int, int, int]:
        name = path.name.lower()
        hint = int(any(k in name for k in ["short", "youtube", "video", "dataset", "slop"]))
        ext_pref = {
            ".json": 4, ".jsonl": 4, ".csv": 3, ".tsv": 3, ".parquet": 2
        }.get(path.suffix.lower(), 1)
        return (hint, ext_pref, -len(name))

    for cand in sorted(candidates, key=_rank, reverse=True):
        try:
            suffix = cand.suffix.lower()
            if suffix == ".csv":
                return pd.read_csv(cand)
            if suffix == ".tsv":
                return pd.read_csv(cand, sep="\t")
            if suffix in {".json", ".jsonl"}:
                try:
                    return pd.read_json(cand)
                except ValueError:
                    # Some JSON files are newline-delimited.
                    return pd.read_json(cand, lines=True)
            if suffix == ".parquet":
                return pd.read_parquet(cand)
            if suffix in {".xls", ".xlsx", ".xlsm", ".xlsb", ".ods", ".odf", ".odt"}:
                return pd.read_excel(cand)
        except Exception:
            continue

    raise ValueError(f"Unable to load a tabular file from dataset download: {root}")


def ai_ids_from_dataset(df: pd.DataFrame) -> set[str]:
    # Reuse nested schema logic from the AI slop dataset.
    ids: set[str] = set()
    required = {"shorts", "labels"}
    if required.issubset({str(c) for c in df.columns}):
        for _, row in df.iterrows():
            labels = row.get("labels")
            shorts = row.get("shorts")
            ai_channel = False
            if isinstance(labels, dict):
                vals = labels.get("slopChannel")
                if isinstance(vals, list):
                    joined = " ".join(str(v).lower() for v in vals)
                    ai_channel = any(k in joined for k in ["slop", "suspected_ai", "ai", "synthetic"])
            if ai_channel and isinstance(shorts, list):
                for item in shorts:
                    if isinstance(item, dict):
                        vid = item.get("videoId")
                        if isinstance(vid, str) and vid.strip():
                            ids.add(vid.strip())
        return ids

    # Generic fallback: if nested schema not present.
    url_col = pick_url_column(df)
    for raw in df[url_col].astype(str):
        if not is_shorts_like_url(raw):
            continue
        vid = extract_video_id(raw)
        if vid:
            ids.add(vid)
    return ids


def human_ids_from_dataset(
    df: pd.DataFrame,
    explicit_id_col: str = "",
    allow_non_youtube_ids: bool = False,
) -> set[str]:
    ids: set[str] = set()
    cols = [str(c) for c in df.columns]
    rejected_non_strict = 0

    def _add_if_valid(raw: object) -> None:
        nonlocal rejected_non_strict
        vid = extract_video_id_from_text(raw)
        if not vid:
            return
        if not allow_non_youtube_ids and not is_strict_youtube_id(vid):
            rejected_non_strict += 1
            return
        ids.add(vid)

    # 1) URL-based extraction if a URL-like column exists.
    try:
        url_col = pick_url_column(df)
        for raw in df[url_col].astype(str):
            _add_if_valid(raw)
    except Exception:
        pass

    # 2) ID-column extraction fallback for datasets with only video_id fields.
    if not ids:
        id_cols = [
            c
            for c in cols
            if re.search(r"(video.?id|short.?id|yt.?id|youtube.?id|(^|_)id$)", c, flags=re.IGNORECASE)
        ]
        if explicit_id_col:
            id_cols = [explicit_id_col] + [c for c in id_cols if c != explicit_id_col]
        for col in id_cols:
            if col not in df.columns:
                continue
            for raw in df[col]:
                _add_if_valid(raw)

    if not ids:
        raise ValueError(
            "Could not extract any human video IDs from dataset. "
            f"Columns detected: {cols[:30]}"
        )
    if rejected_non_strict:
        print(f"Skipped non-YouTube human IDs: {rejected_non_strict}")
    return ids


def main() -> None:
    parser = argparse.ArgumentParser(description="Build mixed eval labels (AI + human Shorts) from Kaggle datasets")
    parser.add_argument("--ai-dataset", default="aibuttonfoundation/youtube-ai-slop-shorts-dataset")
    parser.add_argument("--ai-file-path", default="")
    parser.add_argument("--human-dataset", default="prince7489/youtube-shorts-performance-dataset")
    parser.add_argument("--human-file-path", default="")
    parser.add_argument("--human-id-column", default="video_id", help="Preferred human dataset ID column name")
    parser.add_argument(
        "--human-labels-json",
        default="",
        help="Optional prebuilt human labels JSON (video_id -> human_generated). If set, skips human Kaggle dataset ingestion.",
    )
    parser.add_argument(
        "--allow-non-youtube-human-ids",
        action="store_true",
        help="Allow human IDs that are not strict 11-char YouTube IDs (not recommended for authenticity eval).",
    )
    parser.add_argument("--output", default="app/data/eval_mixed_labels.json")
    parser.add_argument("--max-ai", type=int, default=0, help="Optional cap for AI sample size (0 = no cap)")
    parser.add_argument("--max-human", type=int, default=0, help="Optional cap for human sample size (0 = no cap)")
    args = parser.parse_args()

    ai_df = load_dataset(args.ai_dataset, args.ai_file_path)

    if ai_df is None or ai_df.empty:
        raise ValueError("AI dataset load failed or returned empty dataframe.")

    ai_ids = sorted(ai_ids_from_dataset(ai_df))
    if args.human_labels_json:
        human_map = json.loads(Path(args.human_labels_json).read_text())
        human_ids = sorted(
            vid
            for vid, lab in human_map.items()
            if str(lab).strip().lower() == "human_generated" and is_strict_youtube_id(str(vid))
        )
    else:
        human_df = load_dataset(args.human_dataset, args.human_file_path)
        if human_df is None or human_df.empty:
            raise ValueError("Human dataset load failed or returned empty dataframe.")
        human_ids = sorted(
            human_ids_from_dataset(
                human_df,
                explicit_id_col=args.human_id_column,
                allow_non_youtube_ids=args.allow_non_youtube_human_ids,
            )
        )

    if args.max_ai > 0:
        ai_ids = ai_ids[: args.max_ai]
    if args.max_human > 0:
        human_ids = human_ids[: args.max_human]

    labels: dict[str, str] = {}
    for vid in human_ids:
        labels[vid] = "human_generated"

    conflicts = 0
    for vid in ai_ids:
        if vid in labels and labels[vid] != "ai_generated":
            conflicts += 1
        labels[vid] = "ai_generated"  # AI label wins on overlap.

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(labels, indent=2, sort_keys=True) + "\n")

    ai_count = sum(1 for v in labels.values() if v == "ai_generated")
    human_count = sum(1 for v in labels.values() if v == "human_generated")
    if human_count == 0:
        raise ValueError(
            "Human dataset produced zero labels. Provide --human-file-path with the correct file or verify schema."
        )
    print(f"Saved mixed labels to: {out}")
    print(f"AI labels: {ai_count}")
    print(f"Human labels: {human_count}")
    print(f"Overlap conflicts resolved (AI wins): {conflicts}")
    if human_ids:
        print(f"Sample human IDs: {human_ids[:5]}")


if __name__ == "__main__":
    main()
