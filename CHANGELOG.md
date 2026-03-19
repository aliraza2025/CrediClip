# Changelog

All notable project updates are summarized here in a release-style format.

## 2026-03-19 - Multi-Platform Pipeline and Runtime Stabilization

This release consolidates the work done across YouTube, Instagram, TikTok, queueing, workers, scoring, dashboard, and runtime optimization.

### Added

- Instagram-specific ingestion pipeline
- TikTok-specific ingestion pipeline
- queue job reuse for duplicate URL submissions
- persistent queue storage on Fly using `/data/jobs.db`
- split worker lanes:
  - 2 non-YouTube workers for Instagram/TikTok
  - 1 dedicated YouTube worker
- interactive admin/dashboard view with:
  - worker lane visibility
  - platform mix
  - job detail panel
  - activity feed
  - model mode badges
  - degraded YouTube ingest badges
- OpenAI claim-assessment support with platform and evidence gating
- production-state and benchmark reporting artifacts

### Changed

- moved heavy platform ingestion to worker-backed flows
- made scoring more evidence-aware and less overconfident on sparse inputs
- changed analyzer UX to queue-native polling for link-only submissions
- improved Instagram and TikTok runtime by:
  - reusing warm Chromium browser processes
  - adding fast-mode cutoffs for early exit
  - trimming fallback waits and OCR loops
- changed YouTube behavior to degrade faster on bot-blocked ingest instead of timing out as often

### Fixed

- duplicate in-flight jobs for repeated URL submissions
- non-durable queue state on Fly
- YouTube no longer blocks Instagram and TikTok by sharing the same worker lane
- TikTok media URL handling for browser-discovered URLs
- Instagram worker evidence accounting preservation for OCR/ASR coverage

### Current Platform Status

- **Instagram**: strongest current path with worker-backed browser ingest, OCR, ASR, and gated OpenAI claim assessment
- **TikTok**: stable and usable with tuned worker runtime and browser/media recovery
- **YouTube Shorts**: supported and isolated, but still the weakest lane because of anti-bot and auth friction

### Current Production Baseline

- deterministic scoring remains in app code
- OpenAI is used selectively where it improves claim assessment quality
- queue and worker infrastructure are stable
- dashboard is suitable for admin/demo use
- runtime is materially improved, especially on richer Instagram jobs

### Reference Documents

- [README.md](/Users/aliraaza/Documents/New%20project/README.md)
- [current_production_state_20260319.md](/Users/aliraaza/Documents/New%20project/reports/current_production_state_20260319.md)
- [runtime_benchmark_20260319.json](/Users/aliraaza/Documents/New%20project/reports/runtime_benchmark_20260319.json)
