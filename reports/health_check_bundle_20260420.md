# Health Check Bundle

Date: 2026-04-20

This file is a quick index for the latest live system checks and the Instagram recovery attempt.

## Open These First

1. [operational_health_report_20260420.md](/Users/aliraaza/Documents/New%20project/reports/operational_health_report_20260420.md)
2. [live_health_10_20260420/links_batch_10_20260420T204650Z.csv](/Users/aliraaza/Documents/New%20project/reports/live_health_10_20260420/links_batch_10_20260420T204650Z.csv)
3. [instagram_health_rerun_20260420/links_batch_4_20260420T221136Z.csv](/Users/aliraaza/Documents/New%20project/reports/instagram_health_rerun_20260420/links_batch_4_20260420T221136Z.csv)

## What Was Checked

- live Fly app queue health
- Oracle worker machine availability
- all worker service states
- recent worker log errors
- full local diagnostics
- live 10-link cross-platform health sample
- Instagram cookie refresh and Instagram rerun

## Key Results

### Infrastructure

- Oracle machine is on
- all 3 worker services are active
- queue is healthy and idle
- local diagnostics passed: `31 passed, 0 failed`

### 10-Link Live Health Sample

Artifacts:

- [live_health_10_20260420/links_batch_10_20260420T204650Z.csv](/Users/aliraaza/Documents/New%20project/reports/live_health_10_20260420/links_batch_10_20260420T204650Z.csv)
- [live_health_10_20260420/links_batch_10_20260420T204650Z.json](/Users/aliraaza/Documents/New%20project/reports/live_health_10_20260420/links_batch_10_20260420T204650Z.json)

Summary:

- `10/10` completed
- TikTok looked healthiest
- YouTube completed cleanly but remained low-evidence
- Instagram was degraded on most tested links

Platform averages from that sample:

- Instagram: `avg_score=52.89`, `avg_tokens=4.25`
- TikTok: `avg_score=61.00`, `avg_tokens=25.33`
- YouTube Shorts: `avg_score=57.82`, `avg_tokens=22.33`

### Instagram Cookie Refresh + Rerun

Cookie file used:

- `/Users/aliraaza/Downloads/www.instagram.com_cookies.txt`

Live action taken:

- uploaded fresh Instagram cookie to Oracle
- refreshed `INSTAGRAM_COOKIE_FILE=/home/ubuntu/instagram_cookies.txt`
- restarted non-YouTube workers

Rerun artifacts:

- [instagram_health_rerun_20260420/links_batch_4_20260420T221136Z.csv](/Users/aliraaza/Documents/New%20project/reports/instagram_health_rerun_20260420/links_batch_4_20260420T221136Z.csv)
- [instagram_health_rerun_20260420/links_batch_4_20260420T221136Z.json](/Users/aliraaza/Documents/New%20project/reports/instagram_health_rerun_20260420/links_batch_4_20260420T221136Z.json)

Result:

- partial recovery
- Instagram rerun average improved to:
  - `avg_score=63.19`
  - `avg_tokens=145`

Important detail:

- 1 of the 4 Instagram links recovered strongly:
  - `DTvWvaFj3TP`
  - `567` evidence tokens
  - `high` evidence
  - score `93.13`

But 2 links still came back with zero usable evidence, so Instagram is improved but still inconsistent.

## Best Current Interpretation

- system overall is up and healthy
- queue and workers are functioning
- TikTok is healthy
- YouTube is stable but still thin-evidence
- Instagram improved after the cookie refresh, but is still only partially restored

## Recommended Review Order

If you only open a few files, use this order:

1. [operational_health_report_20260420.md](/Users/aliraaza/Documents/New%20project/reports/operational_health_report_20260420.md)
2. [health_check_bundle_20260420.md](/Users/aliraaza/Documents/New%20project/reports/health_check_bundle_20260420.md)
3. [live_health_10_20260420/links_batch_10_20260420T204650Z.csv](/Users/aliraaza/Documents/New%20project/reports/live_health_10_20260420/links_batch_10_20260420T204650Z.csv)
4. [instagram_health_rerun_20260420/links_batch_4_20260420T221136Z.csv](/Users/aliraaza/Documents/New%20project/reports/instagram_health_rerun_20260420/links_batch_4_20260420T221136Z.csv)
