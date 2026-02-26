#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Retrain generation labels and merge manual overrides")
    parser.add_argument("--file-path", default="youtube_ai_slop_dataset.json")
    parser.add_argument("--dataset", default="aibuttonfoundation/youtube-ai-slop-shorts-dataset")
    parser.add_argument("--labels", default="app/data/generation_labels.json")
    parser.add_argument("--overrides", default="app/data/manual_generation_overrides.json")
    args = parser.parse_args()

    cmd = [
        "python",
        "scripts/train_generation_labels.py",
        "--dataset",
        args.dataset,
        "--file-path",
        args.file_path,
        "--output",
        args.labels,
    ]
    subprocess.run(cmd, check=True)

    labels_path = Path(args.labels)
    labels = json.loads(labels_path.read_text()) if labels_path.exists() else {}

    overrides_path = Path(args.overrides)
    if overrides_path.exists():
        overrides = json.loads(overrides_path.read_text())
    else:
        overrides = {}

    labels.update({str(k): str(v) for k, v in overrides.items()})
    labels_path.write_text(json.dumps(labels, indent=2, sort_keys=True) + "\n")

    print(f"Merged overrides: {len(overrides)}")
    print(f"Final labels count: {len(labels)}")


if __name__ == "__main__":
    main()
