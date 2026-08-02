---
id: TASK-7
title: Investigate an LLM second pass over dictation transcripts
status: In Progress
assignee: []
created_date: '2026-08-02 03:49'
updated_date: '2026-08-02 04:41'
labels: []
dependencies: []
documentation:
  - doc-5
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
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Define what a dictation second pass should and should not do, separating safe deterministic cleanup from semantic rewriting, with fail-safe behavior on ambiguity and no answering or executing dictated content.
- [ ] #2 Compare credible current hosted and local approaches from primary sources, separating measured facts, vendor claims, third-party measurements, and estimates, across latency, streaming, quality, cost, privacy, reliability, and integration complexity.
- [ ] #3 Assess from read-only inspection whether a local model can coexist with Whisper large-v3-turbo Q8 on the upgraded machine, considering VRAM/GTT, RAM, CPU, residency, contention, quantization, and cold versus warm latency.
- [ ] #4 Propose an evaluation plan with representative synthetic examples, an error taxonomy, semantic-preservation checks, p50/p95 latency budgets, quality thresholds, timeout behavior, cost assumptions, a no-second-pass baseline, and deterministic non-LLM cleanup candidates.
- [ ] #5 Recommend one preferred architecture and one or two alternatives with pipeline position, streaming behavior, timeout/failure behavior, auto-submit and overlay behavior, a staged experiment plan, and explicit stop or rollback criteria.
- [ ] #6 Deliver the findings as one cited, confidence-tagged Backlog research document attached to this task through a chore branch and pull request.
<!-- AC:END -->
