#!/usr/bin/env python3
"""Train generation-origin labels from Kaggle datasets (AI + optional human)."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd


SUPPORTED_SUFFIXES = {
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".xml",
    ".parquet",
    ".feather",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".db3",
    ".s3db",
    ".dl3",
    ".xls",
    ".xlsx",
    ".xlsm",
    ".xlsb",
    ".odf",
    ".ods",
    ".odt",
}
STRICT_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
LOOSE_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,20}$")


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


def extract_video_id_from_text(value: object) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    vid = extract_video_id(raw)
    if vid:
        return vid
    if STRICT_VIDEO_ID_RE.fullmatch(raw):
        return raw
    m = re.search(r"\b[A-Za-z0-9_-]{11}\b", raw)
    if m:
        return m.group(0)
    token = raw.split()[0].strip().strip(",.;:()[]{}<>\"'")
    if LOOSE_VIDEO_ID_RE.fullmatch(token):
        return token
    return None


def is_shorts_url(url: str) -> bool:
    if not isinstance(url, str):
        return False
    lower = url.lower()
    return "youtube.com/shorts/" in lower or "youtu.be/" in lower


def _pick_url_column(df: pd.DataFrame) -> str:
    candidates = [c for c in df.columns if re.search(r"(url|link|video)", str(c), flags=re.IGNORECASE)]
    if not candidates:
        raise ValueError("Could not detect a URL/video column in dataset.")

    best = None
    best_score = -1
    for col in candidates:
        values = df[col].astype(str).head(400)
        score = sum("youtube.com" in v.lower() or "youtu.be" in v.lower() for v in values)
        if score > best_score:
            best_score = score
            best = col
    return str(best)


def _pick_label_column(df: pd.DataFrame) -> str:
    candidates = [c for c in df.columns if re.search(r"(label|class|ai|generated|origin|type)", str(c), flags=re.IGNORECASE)]
    if not candidates:
        raise ValueError("Could not detect an AI label column in dataset.")
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
    return bool(re.search(r"\b(ai|synthetic|generated|fake|deepfake)\b", s))


def _rank_candidate(path: Path) -> tuple[int, int, int]:
    name = path.name.lower()
    hint = int(any(k in name for k in ["short", "youtube", "video", "dataset", "slop", "label"]))
    ext_pref = {".json": 4, ".jsonl": 4, ".csv": 3, ".tsv": 3, ".parquet": 2}.get(path.suffix.lower(), 1)
    return (hint, ext_pref, -len(name))


def _read_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix == ".tsv":
        return pd.read_csv(path, sep="\t")
    if suffix in {".json", ".jsonl"}:
        try:
            return pd.read_json(path)
        except ValueError:
            return pd.read_json(path, lines=True)
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix in {".xls", ".xlsx", ".xlsm", ".xlsb", ".ods", ".odf", ".odt"}:
        return pd.read_excel(path)
    raise ValueError(f"Unsupported table format for file: {path}")


def load_dataset(dataset: str, file_path: str) -> pd.DataFrame:
    import kagglehub
    from kagglehub import KaggleDatasetAdapter

    if file_path:
        return kagglehub.load_dataset(
            KaggleDatasetAdapter.PANDAS,
            dataset,
            file_path,
        )

    root = Path(kagglehub.dataset_download(dataset))
    candidates = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES]
    if not candidates:
        raise ValueError(f"No supported tabular files found in {root}")

    for candidate in sorted(candidates, key=_rank_candidate, reverse=True):
        try:
            return _read_table(candidate)
        except Exception:
            continue
    raise ValueError(f"Could not load a tabular file from {root}. Provide --file-path explicitly.")


def ai_ids_from_dataset(df: pd.DataFrame) -> set[str]:
    ids: set[str] = set()
    required = {"shorts", "labels"}
    if required.issubset({str(c) for c in df.columns}):
        for _, row in df.iterrows():
            label_obj = row.get("labels")
            shorts_obj = row.get("shorts")
            ai_channel = False
            if isinstance(label_obj, dict):
                vals = label_obj.get("slopChannel")
                if isinstance(vals, list):
                    joined = " ".join(str(v).lower() for v in vals)
                    ai_channel = any(k in joined for k in ["slop", "suspected_ai", "ai", "synthetic"])
            if ai_channel and isinstance(shorts_obj, list):
                for item in shorts_obj:
                    if isinstance(item, dict):
                        vid = item.get("videoId")
                        if isinstance(vid, str) and vid.strip():
                            ids.add(vid.strip())
        return ids

    url_col = _pick_url_column(df)
    label_col = _pick_label_column(df)
    rows = df[[url_col, label_col]].dropna()
    rows = rows[rows[url_col].astype(str).map(is_shorts_url)]
    rows = rows[rows[label_col].map(_ai_truthy)]
    for raw in rows[url_col].astype(str):
        vid = extract_video_id(raw)
        if vid:
            ids.add(vid)
    return ids


def human_ids_from_dataset(df: pd.DataFrame, id_column: str, allow_non_youtube_ids: bool) -> tuple[set[str], int]:
    ids: set[str] = set()
    skipped_non_strict = 0

    def _add(raw: object) -> None:
        nonlocal skipped_non_strict
        vid = extract_video_id_from_text(raw)
        if not vid:
            return
        if not allow_non_youtube_ids and not STRICT_VIDEO_ID_RE.fullmatch(vid):
            skipped_non_strict += 1
            return
        ids.add(vid)

    try:
        url_col = _pick_url_column(df)
        for raw in df[url_col].astype(str):
            _add(raw)
    except Exception:
        pass

    id_candidates = [id_column] if id_column else []
    for col in df.columns:
        c = str(col)
        if c not in id_candidates and re.search(r"(video.?id|short.?id|yt.?id|youtube.?id|(^|_)id$)", c, flags=re.IGNORECASE):
            id_candidates.append(c)

    for col in id_candidates:
        if col not in df.columns:
            continue
        for raw in df[col]:
            _add(raw)
    return ids, skipped_non_strict


def main() -> None:
    parser = argparse.ArgumentParser(description="Train generation label map from AI + human Kaggle datasets")
    parser.add_argument("--ai-dataset", default="aibuttonfoundation/youtube-ai-slop-shorts-dataset")
    parser.add_argument("--ai-file-path", default="")
    parser.add_argument("--human-dataset", default="prince7489/youtube-shorts-performance-dataset")
    parser.add_argument("--human-file-path", default="")
    parser.add_argument("--human-id-column", default="video_id")
    parser.add_argument("--allow-non-youtube-human-ids", action="store_true")
    parser.add_argument("--skip-human", action="store_true", help="Train with AI dataset only")
    parser.add_argument("--max-ai", type=int, default=0)
    parser.add_argument("--max-human", type=int, default=0)
    parser.add_argument("--output", default="app/data/generation_labels.json")
    args = parser.parse_args()

    ai_df = load_dataset(args.ai_dataset, args.ai_file_path)
    if ai_df is None or ai_df.empty:
        raise ValueError("AI dataset load failed or returned empty dataframe.")
    ai_ids = sorted(ai_ids_from_dataset(ai_df))
    if args.max_ai > 0:
        ai_ids = ai_ids[: args.max_ai]

    human_ids: list[str] = []
    skipped_non_strict = 0
    if not args.skip_human:
        human_df = load_dataset(args.human_dataset, args.human_file_path)
        if human_df is None or human_df.empty:
            raise ValueError("Human dataset load failed or returned empty dataframe.")
        human_set, skipped_non_strict = human_ids_from_dataset(
            human_df,
            id_column=args.human_id_column,
            allow_non_youtube_ids=args.allow_non_youtube_human_ids,
        )
        human_ids = sorted(human_set)
        if args.max_human > 0:
            human_ids = human_ids[: args.max_human]

    labels: dict[str, str] = {}
    for vid in human_ids:
        labels[vid] = "human_generated"

    conflicts = 0
    for vid in ai_ids:
        if vid in labels and labels[vid] != "ai_generated":
            conflicts += 1
        labels[vid] = "ai_generated"

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(labels, indent=2, sort_keys=True) + "\n")

    ai_count = sum(1 for value in labels.values() if value == "ai_generated")
    human_count = sum(1 for value in labels.values() if value == "human_generated")

    print(f"Saved generation labels to: {out}")
    print(f"AI labels: {ai_count}")
    print(f"Human labels: {human_count}")
    print(f"Overlap conflicts resolved (AI wins): {conflicts}")
    if skipped_non_strict:
        print(f"Skipped non-YouTube human IDs: {skipped_non_strict}")
    if not args.skip_human and human_count == 0:
        print(
            "Warning: no strict YouTube human IDs were extracted. "
            "Use --allow-non-youtube-human-ids for synthetic IDs (not useful for live URL overrides)."
        )


if __name__ == "__main__":
    main()
