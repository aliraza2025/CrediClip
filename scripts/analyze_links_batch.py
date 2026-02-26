#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def run_api(api_url: str, url: str, timeout: int = 45) -> dict:
    clean_url = re.sub(r"\\([?=&/])", r"\1", url.strip())
    payload = json.dumps({"url": clean_url})
    cmd = [
        "curl",
        "-sS",
        "-m",
        str(timeout),
        "-X",
        "POST",
        api_url,
        "-H",
        "Content-Type: application/json",
        "-d",
        payload,
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        return {"url": clean_url, "error": p.stderr.strip() or f"curl exit {p.returncode}"}

    try:
        data = json.loads(p.stdout)
    except Exception:
        return {"url": clean_url, "error": "invalid_json", "raw": p.stdout[:300]}

    flags = {f.get("type"): f for f in data.get("flags", [])}
    gen = flags.get("generation_origin", {})
    claim = (data.get("claim_assessments") or [{}])[0]
    return {
        "url": clean_url,
        "credibility_score": data.get("credibility_score"),
        "generation_origin_level": gen.get("level"),
        "generation_origin_score": gen.get("score"),
        "generation_origin_rationale": gen.get("rationale"),
        "top_claim_status": claim.get("status"),
        "top_claim_confidence": claim.get("confidence"),
        "notes": " | ".join((data.get("notes") or [])[:6]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze a batch of video links via /api/analyze")
    parser.add_argument("--api-url", default="https://crediclip-axraza-msba.fly.dev/api/analyze")
    parser.add_argument("--links-file", required=True, help="Text file with one URL per line")
    parser.add_argument("--outdir", default="reports")
    parser.add_argument("--timeout", type=int, default=45)
    args = parser.parse_args()

    links = [ln.strip() for ln in Path(args.links_file).read_text().splitlines()]
    links = [u for u in links if u and not u.startswith("#")]
    if not links:
        raise ValueError("No links found in links file.")

    rows = [run_api(args.api_url, u, timeout=args.timeout) for u in links]

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    json_path = outdir / f"links_batch_{len(rows)}_{ts}.json"
    csv_path = outdir / f"links_batch_{len(rows)}_{ts}.csv"

    json_path.write_text(json.dumps({"api_url": args.api_url, "results": rows}, indent=2) + "\n")

    fields = sorted({k for r in rows for k in r.keys()})
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    ok = [r for r in rows if "error" not in r]
    print(f"Saved JSON: {json_path}")
    print(f"Saved CSV:  {csv_path}")
    print(f"Success: {len(ok)}/{len(rows)}")


if __name__ == "__main__":
    main()
