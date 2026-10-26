---
layout: article
title: What 780 completed video jobs taught me about AI product quality
description: How evidence coverage, readiness benchmarks, and explicit uncertainty shaped the CrediClip roadmap.
author: Ali Raza
date: 2026-10-26
read_time: 6 min read
permalink: /evaluating-ai-product-quality/
note_label: AI product evaluation
note_number: "02"
tags:
  - AI Product Management
  - Evaluation
  - Evidence Extraction
  - FastAPI
toc:
  - id: the-benchmark-that-redirected-the-roadmap
    label: Readiness benchmarks
  - id: evidence-recovery-became-a-product-concern
    label: Evidence recovery
  - id: the-architecture-had-to-support-the-extraction-work
    label: Architecture decisions
  - id: showing-uncertainty-in-the-output
    label: Showing uncertainty
  - id: three-questions-for-evaluating-the-product
    label: Evaluation lessons
  - id: sources-and-further-reading
    label: Sources
---

A completed job is an easy result to count. In an AI application, understanding what that completed job contains takes more work.

I encountered that distinction while building CrediClip, a system that accepts a public short-form video link and returns a credibility score, risk flags, claim assessments, and an evidence-coverage breakdown. The product brought together several different problems: getting access to a video, recovering its text and speech, assessing claims, and presenting a result a person could interpret.

In the March 11, 2026 YouTube batch, all 780 jobs eventually completed, including two retried links. Yet 759 of 780 results, or 97.3%, were flagged as low evidence. The pipeline finished its work while recovering little information from most inputs. That result became a useful way to explain what the evaluation established and where the product still needed improvement.

## The benchmark that redirected the roadmap

Before expanding the product, I ran a balanced, seeded benchmark of 100 videos against four readiness gates on March 4, 2026. Ingestion success was 46%, against a threshold of at least 85%. Among the 46 inputs with successful ingestion, 45 had insufficient evidence for claim verification: 97.8%, against a target of no more than 40%. Score variation also fell below the chosen threshold. The supplied queue reliability measurement passed at 97%.

These were readiness thresholds I had chosen for the project, rather than universal standards for credibility products. Their value was in making a decision concrete. A functioning queue did not resolve the lack of evidence. Changing score weights would still leave the system working with sparse inputs.

The results redirected my effort toward ingestion: browser rendering, on-screen text extraction, speech recognition, and platform-specific recovery paths. Evaluation gave me a reason to change the roadmap and a way to explain that choice.

## Evidence recovery became a product concern

Short-form video exposes important information through several channels. A caption may contain context, speech may contain the main claim, and text on a frame may carry information missing from both. Losing a channel can change what the system is able to assess.

The extraction paths also differed by platform. Public metadata could be thin, browser rendering could recover additional text, and media access could determine whether speech recognition was possible. I brought those outputs into a shared evidence structure so the product could show what had been recovered.

In the 19-link Instagram benchmark comparing March 11 and March 13, 2026, average evidence tokens increased from 1.0 to 130.9 after the ingestion redesign. That was an improvement in recovered evidence on a small benchmark. It did not measure whether credibility judgments had become more accurate. Keeping that distinction attached to the number matters when describing the result.

## The architecture had to support the extraction work

The first version performed heavy processing inside the web process. Video downloads, headless browsers, and speech recognition created memory pressure on the small hosting instance.

I separated the lightweight FastAPI control plane from the workers doing heavy extraction. The web tier handled requests, queue state, scoring, and results. Workers handled platform-specific ingestion and returned artifacts for assessment.

I also separated YouTube work into its own lane after slow or blocked jobs interfered with the other platforms. This was a decision about the user’s wait as well as infrastructure. An unpredictable dependency on one platform should not hold up unrelated work.

## Showing uncertainty in the output

CrediClip needed a way to represent an assessment made from limited evidence. I kept the final score in deterministic application code and used an analysis-confidence value to control how far the result moved from a neutral anchor of 52.

When evidence is thin, that rule keeps the score near the anchor. The interface also presents evidence coverage so a reader can see the basis for the assessment. This is a designed behavior; its usefulness and calibration still need stronger evaluation. A precise-looking score does not by itself establish a precise judgment.

LLMs have a narrower role in the workflow. They can produce structured claim assessments when evidence thresholds are met. They do not directly write the final credibility number. That boundary makes the scoring rule easier to inspect and keeps the role of the model explicit.

## Three questions for evaluating the product

The work left me with three separate questions. Did the processing complete? Did it recover enough relevant evidence? Did the assessment match an independently established judgment?

The 780-link batch answered the first question for that run and exposed limitations in the second. It did not settle the third. Answering that would require stronger labeled evaluation, careful separation of calibration and test data, and reporting across platforms and content types.

Several limitations remain in CrediClip: uneven platform access, hand-set scoring weights, restricted retrieval coverage, and small labeled evaluations. The case study also discloses curated presentation examples. Those examples are separate from the reported batch results.

For me, the product lesson was to connect each metric to a decision. Completion helped evaluate the processing workflow. Evidence coverage helped identify the next ingestion work. Stronger independent evaluation is needed to assess the credibility judgments themselves.

Building the workflow and documenting those distinctions gave me a clearer view of what I had shipped, what its outputs meant, and what I needed to learn next.

## Sources and further reading

This article adapts the evaluation and product lessons in my [original CrediClip case study](https://alirazaportfolio2026.netlify.app/crediclip).

- [780-link YouTube batch summary](https://github.com/aliraza2025/CrediClip/blob/main/reports/links_batch_780_20260311T175959Z_final_summary.json), including evidence coverage and retried links.
- [March 4 readiness benchmark](https://github.com/aliraza2025/CrediClip/blob/main/reports/phase1_benchmark_100_20260304T111624Z.json), including gate thresholds and counts.
- [Instagram benchmark summary](https://github.com/aliraza2025/CrediClip/blob/main/reports/instagram_phase2_summary_20260313.json), with the before and after reports.
- [Building the CrediClip data plane]({{ '/data-plane/' | relative_url }}), the accompanying architecture article.
- [Source code](https://github.com/aliraza2025/CrediClip) and [my portfolio](https://alirazaportfolio2026.netlify.app/).

