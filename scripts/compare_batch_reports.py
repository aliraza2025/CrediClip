#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path


NUMERIC_FIELDS = [
    "credibility_score",
    "evidence_total_tokens",
    "evidence_caption_tokens",
    "evidence_transcript_tokens",
    "evidence_ocr_tokens",
    "evidence_asr_tokens",
]


def _to_float(value: str | None) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _load_csv(path: Path) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    with path.open() as fh:
        for row in csv.DictReader(fh):
            url = (row.get("url") or "").strip()
            if url:
                rows[url] = row
    return rows


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare before/after batch analysis CSV reports.")
    parser.add_argument("--before", required=True, help="Before CSV report path")
    parser.add_argument("--after", required=True, help="After CSV report path")
    parser.add_argument("--outdir", default="reports")
    args = parser.parse_args()

    before_rows = _load_csv(Path(args.before))
    after_rows = _load_csv(Path(args.after))
    common_urls = sorted(set(before_rows).intersection(after_rows))
    if not common_urls:
        raise SystemExit("No overlapping URLs found between before/after reports.")

    comparison_rows: list[dict] = []
    field_deltas: dict[str, list[float]] = {field: [] for field in NUMERIC_FIELDS}

    for url in common_urls:
        before = before_rows[url]
        after = after_rows[url]
        row = {"url": url}
        for field in NUMERIC_FIELDS:
            before_value = _to_float(before.get(field))
            after_value = _to_float(after.get(field))
            row[f"before_{field}"] = before_value
            row[f"after_{field}"] = after_value
            if before_value is not None and after_value is not None:
                delta = round(after_value - before_value, 4)
                row[f"delta_{field}"] = delta
                field_deltas[field].append(delta)
            else:
                row[f"delta_{field}"] = None
        row["before_notes"] = before.get("notes", "")
        row["after_notes"] = after.get("notes", "")
        comparison_rows.append(row)

    summary = {
        "before": str(Path(args.before)),
        "after": str(Path(args.after)),
        "overlap_count": len(common_urls),
        "avg_deltas": {field: _avg(values) for field, values in field_deltas.items()},
    }

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    json_path = outdir / f"batch_compare_{ts}.json"
    csv_path = outdir / f"batch_compare_{ts}.csv"

    json_path.write_text(json.dumps({"summary": summary, "rows": comparison_rows}, indent=2) + "\n")

    fields = sorted({k for row in comparison_rows for k in row.keys()})
    with csv_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(comparison_rows)

    print(f"Saved JSON: {json_path}")
    print(f"Saved CSV:  {csv_path}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
