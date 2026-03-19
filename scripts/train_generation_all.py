#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path


def run_cmd(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)


def verify_labels(labels_path: Path) -> None:
    if not labels_path.exists():
        raise FileNotFoundError(f"labels file not found: {labels_path}")
    data = json.loads(labels_path.read_text())
    counts = Counter(str(v) for v in data.values())
    print("\nVerification")
    print(f"labels file: {labels_path}")
    print(f"total: {len(data)}")
    print(f"counts: {dict(counts)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "One-command generation pipeline: optional human-label build + optional Kaggle retrain "
            "+ calibrator training + threshold report"
        )
    )
    parser.add_argument("--channels-file", default="app/data/human_channels.txt")
    parser.add_argument("--max-per-channel", type=int, default=40)
    parser.add_argument("--timeout-sec", type=int, default=90)
    parser.add_argument("--human-labels-json", default="app/data/human_generation_labels.json")
    parser.add_argument("--skip-build-human", action="store_true")
    parser.add_argument(
        "--skip-retrain",
        action="store_true",
        help="Skip Kaggle retrain and reuse existing labels file.",
    )

    parser.add_argument("--ai-dataset", default="aibuttonfoundation/youtube-ai-slop-shorts-dataset")
    parser.add_argument("--ai-file-path", default="")
    parser.add_argument("--human-dataset", default="prince7489/youtube-shorts-performance-dataset")
    parser.add_argument("--human-file-path", default="")
    parser.add_argument("--human-id-column", default="video_id")
    parser.add_argument("--allow-non-youtube-human-ids", action="store_true")

    parser.add_argument("--labels", default="app/data/generation_labels.json")
    parser.add_argument("--overrides", default="app/data/manual_generation_overrides.json")
    parser.add_argument(
        "--reports-glob",
        default="reports/validation_*.csv",
        help="Validation report CSV glob used for calibrator training.",
    )
    parser.add_argument("--calibrator-output", default="app/data/generation_calibrator.json")
    args = parser.parse_args()

    if not args.skip_build_human:
        run_cmd(
            [
                sys.executable,
                "scripts/build_human_labels_from_channels.py",
                "--channels-file",
                args.channels_file,
                "--max-per-channel",
                str(args.max_per_channel),
                "--timeout-sec",
                str(args.timeout_sec),
                "--output",
                args.human_labels_json,
            ]
        )

    if not args.skip_retrain:
        retrain_cmd = [
            sys.executable,
            "scripts/retrain_generation_labels.py",
            "--ai-dataset",
            args.ai_dataset,
            "--ai-file-path",
            args.ai_file_path,
            "--human-dataset",
            args.human_dataset,
            "--human-file-path",
            args.human_file_path,
            "--human-id-column",
            args.human_id_column,
            "--skip-human",
            "--human-labels-json",
            args.human_labels_json,
            "--labels",
            args.labels,
            "--overrides",
            args.overrides,
        ]
        if args.allow_non_youtube_human_ids:
            retrain_cmd.append("--allow-non-youtube-human-ids")
        run_cmd(retrain_cmd)
    else:
        print("Skipping Kaggle retrain; using existing labels file.")

    verify_labels(Path(args.labels))

    run_cmd(
        [
            sys.executable,
            "scripts/train_generation_calibrator.py",
            "--labels",
            args.labels,
            "--reports-glob",
            args.reports_glob,
            "--output",
            args.calibrator_output,
        ]
    )
    print(f"Calibrator saved: {args.calibrator_output}")

    run_cmd(
        [
            sys.executable,
            "scripts/evaluate_thresholds.py",
            "--labels",
            args.labels,
            "--report-glob",
            args.reports_glob,
        ]
    )


if __name__ == "__main__":
    main()
