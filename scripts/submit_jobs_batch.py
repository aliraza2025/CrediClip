#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit a batch of video URLs to /api/jobs")
    parser.add_argument("--links-file", required=True)
    parser.add_argument("--api-base", default="https://crediclip-axraza-msba.fly.dev")
    parser.add_argument("--outdir", default="reports")
    parser.add_argument("--timeout-sec", type=int, default=30)
    args = parser.parse_args()

    links = [ln.strip() for ln in Path(args.links_file).read_text().splitlines()]
    links = [u for u in links if u and not u.startswith("#")]
    if not links:
        raise ValueError("No links found in links file.")

    rows: list[dict] = []
    endpoint = args.api_base.rstrip("/") + "/api/jobs"
    with httpx.Client(timeout=args.timeout_sec) as client:
        for url in links:
            try:
                resp = client.post(endpoint, json={"url": url})
                resp.raise_for_status()
                data = resp.json()
                rows.append(
                    {
                        "url": url,
                        "job_id": data.get("id"),
                        "status": data.get("status"),
                        "created_at": data.get("created_at"),
                    }
                )
            except Exception as exc:
                rows.append({"url": url, "error": type(exc).__name__})

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    json_path = outdir / f"jobs_submitted_{len(rows)}_{ts}.json"
    csv_path = outdir / f"jobs_submitted_{len(rows)}_{ts}.csv"

    json_path.write_text(json.dumps({"api_base": args.api_base, "rows": rows}, indent=2) + "\n")
    fields = sorted({k for r in rows for k in r.keys()})
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    ok = [r for r in rows if "error" not in r]
    print(f"Saved JSON: {json_path}")
    print(f"Saved CSV:  {csv_path}")
    print(f"Submitted: {len(ok)}/{len(rows)}")


if __name__ == "__main__":
    main()
