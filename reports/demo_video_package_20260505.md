# CrediClip Demo Video Package

Date: 2026-05-05

## Goal

Create a short live demo video for CrediClip that shows:

1. what the product does
2. one strong result
3. one weak/degraded result
4. the dashboard / worker-backed system

Recommended length:

- `60` to `90` seconds

## Best Demo Structure

### Scene 1: Title

Duration:

- `5` seconds

On screen:

- `CrediClip`
- `AI-Powered Credibility Analysis for Short-Form Video`

Voiceover:

- "This is CrediClip, a system that analyzes short-form video links and returns an evidence-aware credibility score."

### Scene 2: Paste a strong example

Duration:

- `15` to `20` seconds

Use this link:

- `https://www.instagram.com/reel/DTvWvaFj3TP/?igsh=NjFhOGMzYTE3ZQ==`

Expected talking points:

- strong evidence recovery
- high credibility
- worker-backed analysis

Voiceover:

- "Here I’m pasting an Instagram link. CrediClip sends it through the queue, extracts evidence, and returns a structured credibility result."

When result appears, highlight:

- score around `93.13`
- evidence level `high`
- evidence tokens `567`

Voiceover:

- "This example is a strong high-evidence case. The system recovered rich evidence and returned a high credibility score."

### Scene 3: Paste a weak or degraded example

Duration:

- `15` to `20` seconds

Use this link:

- `https://www.instagram.com/reel/DVfVE6ritM1/?igsh=NjFhOGMzYTE3ZQ==`

Expected talking points:

- low evidence
- degraded extraction path
- score stays near neutral because the system avoids fake confidence

Voiceover:

- "Now I’ll show a weaker case. This link returns very little usable evidence, so the system avoids overconfidence and keeps the score near neutral."

When result appears, highlight:

- score around `51.74`
- evidence tokens `0`
- evidence level `low`

Voiceover:

- "This is an important design feature: low evidence does not produce a falsely confident result."

### Scene 4: Dashboard / operations

Duration:

- `15` to `20` seconds

On screen:

- open `/dashboard`
- show queue stats
- show worker lanes
- show recent jobs

Voiceover:

- "The dashboard shows the live operational side of the system: queue health, worker lanes, recent jobs, and whether the system used heuristic or OpenAI-assisted claim assessment."

Optional callout:

- mention split workers:
  - `2` non-YouTube workers
  - `1` YouTube-only worker

### Scene 5: Closing

Duration:

- `5` to `10` seconds

Voiceover:

- "So CrediClip turns a public short-form video link into an evidence-aware credibility assessment, with transparent scoring and platform-specific processing."

## Short Voiceover Script

Use this as a single clean read:

> This is CrediClip, an AI-powered system for credibility analysis of short-form video.  
> It takes a public Instagram, TikTok, or YouTube Shorts link and returns a credibility score, evidence summary, and claim-level assessment.  
> Here is a strong Instagram example. The system extracts rich evidence and returns a high-confidence score.  
> Now here is a weaker example. Because the extracted evidence is limited, the score stays much closer to neutral instead of pretending certainty.  
> Finally, the dashboard shows the live queue, worker lanes, and operational status behind the system.  
> In short, CrediClip turns a video URL into an explainable, evidence-aware trust signal.

## Best Links for Demo

### Strong example

- `https://www.instagram.com/reel/DTvWvaFj3TP/?igsh=NjFhOGMzYTE3ZQ==`

### Weak / degraded example

- `https://www.instagram.com/reel/DVfVE6ritM1/?igsh=NjFhOGMzYTE3ZQ==`

### TikTok healthy example

- `https://www.tiktok.com/@reuters/video/7589372207017626894`

### YouTube thin-evidence example

- `https://www.youtube.com/shorts/AlmK64-o8d4`

## Recording Order

Recommended order:

1. title card
2. strong Instagram example
3. weak Instagram example
4. dashboard
5. close

## Recording Tips

- use the queue-native analyzer flow
- wait until the result fully renders before speaking about the score
- zoom in slightly on:
  - credibility score
  - evidence summary
  - dashboard queue cards
- keep the demo under `90` seconds
- avoid switching between too many platforms in one short video

## Optional Longer Version

If you want a `2` minute demo instead:

1. strong Instagram
2. weak Instagram
3. healthy TikTok
4. YouTube thin-evidence case
5. dashboard close

