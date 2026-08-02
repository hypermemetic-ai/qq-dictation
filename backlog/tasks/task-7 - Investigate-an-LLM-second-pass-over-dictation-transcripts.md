---
id: TASK-7
title: Investigate an LLM second pass over dictation transcripts
status: Done
assignee: []
created_date: '2026-08-02 03:49'
updated_date: '2026-08-02 05:08'
labels: []
dependencies: []
documentation:
  - doc-5
  - doc-6
priority: high
type: spike
ordinal: 6000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Outcome: an evidence-based recommendation on whether qq-dictation should run a language-model second pass over each transcript, optimizing aggressively for low time to first useful output and low total latency while producing a meaningful quality improvement (filler removal, repeated words and double takes, false starts, punctuation, self-corrections and contradictory phrasing where the intended final statement is clear) at relatively low cost. The hosted-vs-local hypothesis is tested, not accepted.

Decision ledger:
- D1 research-only boundary: no implementing, enabling, installing, downloading, or purchasing — explicit operator direction 2026-08-01.
- D2 do not resurrect the retired FDT system; mine its history only as evidence — explicit operator direction 2026-08-01.
- D3 do not upload recordings or transcript history; no inspecting private transcript history without explicit operator agreement; synthetic examples and public data are the default — explicit operator direction 2026-08-01.
- D4 do not entangle this research with the open native-overlay Change (PR 9) — explicit operator direction 2026-08-01.
- D5 investigation plan and its recorded assumptions — operator affirmation 2026-08-01, plans doc-6.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Define what a dictation second pass should and should not do, separating safe deterministic cleanup from semantic rewriting, with fail-safe behavior on ambiguity and no answering or executing dictated content.
- [x] #2 Compare credible current hosted and local approaches from primary sources, separating measured facts, vendor claims, third-party measurements, and estimates, across latency, streaming, quality, cost, privacy, reliability, and integration complexity.
- [x] #3 Assess from read-only inspection whether a local model can coexist with Whisper large-v3-turbo Q8 on the upgraded machine, considering VRAM/GTT, RAM, CPU, residency, contention, quantization, and cold versus warm latency.
- [x] #4 Propose an evaluation plan with representative synthetic examples, an error taxonomy, semantic-preservation checks, p50/p95 latency budgets, quality thresholds, timeout behavior, cost assumptions, a no-second-pass baseline, and deterministic non-LLM cleanup candidates.
- [x] #5 Recommend one preferred architecture and one or two alternatives with pipeline position, streaming behavior, timeout/failure behavior, auto-submit and overlay behavior, a staged experiment plan, and explicit stop or rollback criteria.
- [x] #6 Deliver the findings as one cited, confidence-tagged Backlog research document attached to this task through a chore branch and pull request.
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Delivered doc-5 (research report) + doc-6 (plan) on chore/task-7-second-pass-research. Verdict: a second pass is worth a staged experiment. Preferred: hosted cleanup via Handy's existing post-processing hook (Groq llama-3.1-8b-instant, ZDR on, plus a one-line HTTP-timeout patch); est. +0.3-0.6 s, <$0.10/month at 200 dictations/day. Alternative A: local Qwen3-4B Q4_K_M warm on the incoming RX 6400 via the custom localhost provider (privacy-first; est. +1.8-2.2 s warm; quality unproven until Stage 0). Alternative B: extend the deployed deterministic filter only. Evidence: on-host timing telemetry (n=42, median 12 s audio -> 2.21 s ASR, p90 3 s; Vulkan0 bound, flash, greedy — already at fast defaults); two verified researcher tickets; primary-source pricing/privacy fetches. Whisper-speed answer: zero-code levers are Parakeet Unified / Canary 180M model switch (same Vulkan path, 3-13x published), unload-timeout Never, RX 6400 explicit selection (~1.5-2.5x est). Fresh-context reviewer: SHIP with notes; all eight findings (arithmetic/consistency, none conclusion-changing) verified and fixed in 00eadcf. Boundaries D1-D4 held: no implementation, no downloads, no private transcript access, PR 9 untouched.
<!-- SECTION:FINAL_SUMMARY:END -->
