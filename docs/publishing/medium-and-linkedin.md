# Publishing CrediClip's Data-Plane Article

## Canonical GitHub Pages article

Use this as the original URL after GitHub Pages is enabled:

`https://aliraza2025.github.io/CrediClip/data-plane/`

## Medium

**Title:** Building a Multi-Lane Data Plane for Short-Form Video Credibility Analysis

**Subtitle:** How I separated CrediClip's lightweight control plane from platform-specific evidence workers for YouTube Shorts, Instagram Reels, and TikTok.

**Topics:** Python, FastAPI, Data Engineering, Artificial Intelligence, System Architecture

Publishing steps:

1. Open the source article in `docs/articles/building-crediclip-data-plane.md`.
2. Copy everything after the closing front-matter line (`---`).
3. Paste it into a new Medium story.
4. Set the title and subtitle above.
5. Confirm each Python, Bash, and text diagram block is formatted as code.
6. Add the canonical GitHub Pages URL in Medium's advanced story settings.
7. Keep the article outside the Medium paywall for capstone assessment access.

## LinkedIn teaser

Short-form video credibility analysis is not only a modeling problem. Before a system can score a video, it must reliably collect captions, transcripts, OCR text, audio evidence, and platform metadata.

In my CrediClip capstone, I separated the lightweight control plane from a multi-lane data plane. Dedicated worker lanes isolate YouTube Shorts from Instagram and TikTok, preventing one platform's failures from blocking the entire pipeline.

In this technical article, I explain the queue design, lane-routing logic, platform adapters, failure recovery, and engineering tradeoffs, with code samples from the working implementation.

Read the full article: https://aliraza2025.github.io/CrediClip/data-plane/

#Python #FastAPI #DataEngineering #SystemArchitecture #ArtificialIntelligence

## Submission description

This article focuses on one technical aspect of CrediClip: the data plane. It explains how platform-specific workers collect evidence, how queue lanes isolate workloads, and how the control plane coordinates analysis without performing heavy media processing inside the public request path.
