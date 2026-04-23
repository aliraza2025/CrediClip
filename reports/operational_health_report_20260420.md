# Operational Health Report

Date: 2026-04-20

## Executive Summary

The system is up, the Oracle machine is on, all worker services are running, the queue is healthy, and the live app completed a 10-link cross-platform health sample successfully (`10/10`).

However, the live Instagram path appears to be degraded relative to the earlier worker-backed baseline. The current live responses suggest Instagram link-only analysis is falling back to metadata-only analysis on most tested links instead of using the richer queue-backed worker path.

## 1. Infrastructure Status

### Fly App / Queue

- `queued: 0`
- `processing: 0`
- `completed: 192`
- `failed: 8`

Interpretation:

- queue is idle
- no current backlog
- no stuck jobs
- app is healthy at the API level

### Oracle Worker Machine

- host reachable by SSH
- hostname: `instance-20260228-0948`
- machine is on

### Worker Services

All three worker services are active:

- `crediclip-worker.service`
- `crediclip-worker-2.service`
- `crediclip-worker-youtube.service`

Current worker layout:

- `oracle-worker-1` -> Instagram/TikTok
- `oracle-worker-2` -> Instagram/TikTok
- `oracle-worker-youtube` -> YouTube Shorts only

## 2. Worker Log Health

Reviewed the last 48 hours of worker logs for:

- `claim error`
- `HTTPStatusError`
- `ReadTimeout`
- `ReadError`
- `TimeoutError`

Result:

- no recent intermittent claim/app error pattern found in the last 48 hours
- recent log activity shows clean worker completions

This means the earlier intermittent worker communication errors do not appear to be an active issue right now.

## 3. Local Diagnostic Result

Ran full local diagnostics.

Result:

- `31 passed`
- `0 failed`

This confirms:

- backend routes respond correctly
- queue workflow still passes
- duplicate job reuse still works
- platform parsers still pass
- OpenAI provider selection and gating still pass

## 4. Live 10-Link Health Sample

Artifacts:

- [links_batch_10_20260420T204650Z.csv](/Users/aliraaza/Documents/New%20project/reports/live_health_10_20260420/links_batch_10_20260420T204650Z.csv)
- [links_batch_10_20260420T204650Z.json](/Users/aliraaza/Documents/New%20project/reports/live_health_10_20260420/links_batch_10_20260420T204650Z.json)

Result:

- `10/10` completed successfully

### Platform Summary

#### Instagram

- count: `4`
- avg score: `52.89`
- avg evidence tokens: `4.25`
- max tokens: `17`
- min tokens: `0`

Observed behavior:

- 3 of 4 Instagram links returned effectively no usable evidence
- multiple responses included:
  - `Instagram returned a generic app-shell/error page instead of public post metadata.`
  - `Instagram transcript auto-ingestion is not enabled yet; using metadata-only analysis.`

Interpretation:

- Instagram is currently the weakest live path in this sample
- this is a regression from the earlier worker-backed Instagram baseline
- the most likely explanation is that the live `/api/analyze` path is not currently routing Instagram link-only requests through the richer worker-backed queue flow

#### TikTok

- count: `3`
- avg score: `61.00`
- avg evidence tokens: `25.33`
- max tokens: `70`
- min tokens: `2`

Observed behavior:

- Reuters TikTok completed with medium evidence and OpenAI-assisted claim assessment
- two lower-evidence TikTok links still completed cleanly

Interpretation:

- TikTok is currently the healthiest platform path in this sample
- behavior is stable
- evidence quality varies, but the path is operationally sound

#### YouTube Shorts

- count: `3`
- avg score: `57.82`
- avg evidence tokens: `22.33`
- max tokens: `25`
- min tokens: `19`

Observed behavior:

- all 3 YouTube links completed
- no timeout in this sample
- all 3 were low-evidence outputs

Interpretation:

- YouTube is working operationally
- current behavior is degraded-but-stable
- the earlier timeout problem appears improved, but evidence richness is still limited

## 5. Current Overall Assessment

### Working Well

- Oracle machine is up
- worker services are up
- queue is healthy
- diagnostics are clean
- TikTok is healthy
- YouTube is completing instead of timing out

### Needs Attention

- Instagram appears to have regressed from worker-backed rich analysis to metadata-only analysis on the live path

This is the main issue found in this health check.

## 6. Recommended Next Steps

1. Verify and restore live Instagram queue-backed analysis routing.
   - check `INSTAGRAM_ANALYZE_VIA_QUEUE`
   - confirm `/api/analyze` is still using the worker path for Instagram link-only requests

2. Re-run the same Instagram sample after restoring the worker-backed path.

3. Keep TikTok as the current reference platform for expected healthy live behavior.

4. Keep monitoring YouTube, but it is not the biggest issue from this check.

## Final Verdict

The overall system is online and functioning, but it is not fully “all good” across every platform.

Most important conclusion:

- infrastructure: healthy
- workers: healthy
- diagnostics: healthy
- TikTok: healthy
- YouTube: operational but thin-evidence
- Instagram: currently degraded and likely misconfigured on the live analysis path
