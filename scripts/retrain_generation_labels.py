#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Retrain generation labels and merge manual overrides")
    parser.add_argument("--ai-file-path", default="")
    parser.add_argument("--ai-dataset", default="aibuttonfoundation/youtube-ai-slop-shorts-dataset")
    parser.add_argument("--human-file-path", default="")
    parser.add_argument("--human-dataset", default="prince7489/youtube-shorts-performance-dataset")
    parser.add_argument("--human-id-column", default="video_id")
    parser.add_argument("--allow-non-youtube-human-ids", action="store_true")
    parser.add_argument("--skip-human", action="store_true")
    parser.add_argument(
        "--human-labels-json",
        default="",
        help="Optional JSON mapping of real YouTube IDs -> human_generated to merge after training.",
    )
    parser.add_argument("--labels", default="app/data/generation_labels.json")
    parser.add_argument("--overrides", default="app/data/manual_generation_overrides.json")
    args = parser.parse_args()

    cmd = [
        sys.executable,
        "scripts/train_generation_labels.py",
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
        "--output",
        args.labels,
    ]
    if args.allow_non_youtube_human_ids:
        cmd.append("--allow-non-youtube-human-ids")
    if args.skip_human:
        cmd.append("--skip-human")
    subprocess.run(cmd, check=True)

    labels_path = Path(args.labels)
    labels = json.loads(labels_path.read_text()) if labels_path.exists() else {}

    merged_human = 0
    skipped_human_conflicts = 0
    if args.human_labels_json:
        human_labels_path = Path(args.human_labels_json)
        if human_labels_path.exists():
            human_map = json.loads(human_labels_path.read_text())
            for vid, label in human_map.items():
                if str(label).strip().lower() != "human_generated":
                    continue
                key = str(vid)
                # Keep AI labels when conflicts exist; manual overrides can still force changes.
                if labels.get(key) == "ai_generated":
                    skipped_human_conflicts += 1
                    continue
                labels[key] = "human_generated"
                merged_human += 1
        else:
            print(f"Human labels JSON not found, skipping: {human_labels_path}")

    overrides_path = Path(args.overrides)
    if overrides_path.exists():
        overrides = json.loads(overrides_path.read_text())
    else:
        overrides = {}

    labels.update({str(k): str(v) for k, v in overrides.items()})
    labels_path.write_text(json.dumps(labels, indent=2, sort_keys=True) + "\n")

    if args.human_labels_json:
        print(f"Merged human labels: {merged_human}")
        print(f"Skipped human conflicts (existing AI labels): {skipped_human_conflicts}")
    print(f"Merged overrides: {len(overrides)}")
    print(f"Final labels count: {len(labels)}")


if __name__ == "__main__":
    main()
