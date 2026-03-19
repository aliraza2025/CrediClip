# CrediClip Current Production State

Date: 2026-03-19

## Executive Summary

CrediClip is now running as a multi-platform short-video credibility analysis system with:

- platform-specific ingestion for YouTube Shorts, Instagram, and TikTok
- queue-backed worker processing for heavy extraction
- persistent queue storage on Fly
- split worker lanes so YouTube does not block Instagram/TikTok
- selective OpenAI claim assessment where benchmarks showed it helps
- a live interactive dashboard for admin and demo use

The system is operationally stable. Instagram is currently the strongest platform path, TikTok is solid and improving, and YouTube is architecturally stable but still the weakest due to platform auth and anti-bot friction.

## Current Live Health

- Local diagnostics: 31 passed, 0 failed
- Live queue counts:
  - queued: 0
  - processing: 0
  - completed: 184
  - failed: 8
- Live app and dashboard: responding
- Worker layout:
  - 2 non-YouTube workers for Instagram/TikTok
  - 1 YouTube-only worker

## Architecture

### Web App

Handles:

- link intake
- job creation and reuse
- status polling
- deterministic scoring
- dashboard/admin views

### Queue

- persistent SQLite queue database mounted on Fly at `/data/jobs.db`
- supports long-running jobs without losing queue state on restart

### Workers

- Oracle worker lane 1: Instagram/TikTok
- Oracle worker lane 2: Instagram/TikTok
- Oracle worker lane 3: YouTube only

This split prevents slow YouTube jobs from blocking the other platforms.

## Platform Status

### Instagram

Current status: strongest path

Working:

- browser-rendered recovery
- authenticated worker session
- OCR
- ASR
- OpenAI claim assessment when evidence passes the gate
- fast-mode early exit when enough browser evidence is already present

Notes:

- rich Instagram cases improved significantly in runtime after the recent optimization pass

### TikTok

Current status: stable and usable

Working:

- browser/media discovery
- cookie-aware worker path
- OCR and metadata recovery
- OpenAI available when evidence qualifies
- fast-mode early exit for medium-evidence cases

Notes:

- TikTok is operationally strong
- low-evidence TikTok was already fast, so runtime gains are smaller there
- audio/transcript richness is still weaker than Instagram

### YouTube Shorts

Current status: architecturally correct, operationally weakest

Working:

- dedicated YouTube worker lane
- queue-backed processing
- degraded ingest handling when bot/sign-in blocks are hit

Notes:

- YouTube still sees anti-bot/auth friction
- instead of timing out as often, it now degrades faster to lighter metadata-first analysis

## OpenAI Rollout

Implemented:

- OpenAI claim assessment support in production
- platform-aware and evidence-aware gating
- safer retry and fallback behavior

Current behavior:

- Instagram: strongest OpenAI fit
- TikTok: OpenAI can engage when evidence is strong enough
- YouTube: usually still constrained by ingest quality before OpenAI meaningfully helps

OpenAI is not used to directly generate the final score. The final credibility score remains deterministic in application code.

## Runtime Improvements Completed

Implemented in this optimization pass:

1. duplicate-job reuse
2. queue-native analyzer UI with status polling
3. second non-YouTube worker
4. warm shared Chromium reuse in non-YouTube workers
5. Instagram fast-mode cutoff
6. TikTok fast-mode cutoff

Reference benchmark:

- rich Instagram sample improved from 85.71s to 61.64s
- TikTok Reuters worker probe improved from 33.87s to 23.28s

See:

- `/Users/aliraaza/Documents/New project/reports/runtime_benchmark_20260319.json`

## Dashboard

The dashboard is now a usable admin/demo surface with:

- live queue stats
- worker lane view
- platform mix
- recent jobs table
- job detail panel
- activity feed
- OpenAI vs Heuristic badges
- degraded YouTube ingest badges
- queue pulse and ops summary panels

## What Is Working Well

- app and dashboard availability
- durable queue behavior
- duplicate-job reuse
- parallel non-YouTube processing
- Instagram analysis quality
- TikTok reliability and improved runtime
- local diagnostics and operational visibility

## What Still Needs Attention

- YouTube auth and anti-bot friction
- TikTok evidence richness on some links
- ongoing threshold tuning for when OpenAI should engage

## Recommended Next Steps

1. Treat this as the current production baseline.
2. Keep monitoring Instagram/TikTok latency and evidence quality.
3. If another engineering pass is needed, focus it on YouTube-specific ingest/auth handling rather than more general runtime tuning.
