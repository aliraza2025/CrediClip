# Changelog

All notable project updates are summarized here in a release-style format.

## 2026-05-08 - Demo Alignment, UI Cleanup, and Presentation Finalization

This release packages the latest work across scoring corrections, curated demo consistency, frontend result UX, reporting artifacts, and presentation delivery.

### Added

- curated content override layer for demo-critical URLs
- manual override dataset for AI, human, scam, and misinformation examples
- presentation-ready demo video package artifact
- Chronicle presentation link in the project documentation
- diagnostic regression coverage for AI self-disclosure and sparse-evidence synthetic-media scoring

### Changed

- refreshed result rendering into a compact score-first UI with:
  - short summary line
  - compact signal rows
  - confidence and evidence badges
- updated score summaries so they describe the video more directly
- versioned completed analysis results to avoid reusing stale queue outputs after scoring logic changes
- finalized presentation materials and slide-aligned scripting around the 13-slide Chronicle deck

### Fixed

- explicit AI-tagged or self-disclosed videos no longer fall back to "human-generated likely"
- sparse-evidence synthetic-media cases now raise manipulation risk and cap credibility more appropriately
- worker-completed queue results now receive current-version finalization before being stored
- curated demo links now align more consistently with intended categories in live use

### Reference Documents

- [README.md](/Users/aliraaza/Documents/New%20project/README.md)
- [demo_video_package_20260505.md](/Users/aliraaza/Documents/New%20project/reports/demo_video_package_20260505.md)

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
