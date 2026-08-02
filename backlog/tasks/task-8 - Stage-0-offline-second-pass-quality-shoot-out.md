---
id: TASK-8
title: 'Stage 0: offline second-pass quality shoot-out'
status: To Do
assignee: []
created_date: '2026-08-02 13:55'
labels: []
dependencies: []
priority: high
type: task
ordinal: 7000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Outcome (RESHAPED by operator 2026-08-02: skip the full offline shoot-out as a gate — 'just do it and then test'): a minimal smoke glance (built harness, ~15 corpus items, eyeball outputs for answered-questions/injection/correction-flips) then a live real-use trial of Cerebras gpt-oss-120b in Handy with the HTTP-timeout patch, then keep-or-kill by the operator. The built corpus/runner/scorer become the smoke-test tool, not a gate. Approved by the operator 2026-08-02: Groq + OpenAI approved, DeepSeek V4 Flash (via OpenRouter) as third arm if latency-sane, <$1 total spend.

Candidates (FINAL per operator 2026-08-02, Cerebras-only; OpenAI, Groq, DeepSeek arms all dropped by operator): Arm A = Cerebras gpt-oss-120b (production; ~3000 tok/s vendor-rated; reasoning_effort=low; structured output; $0.35/$0.75 per Mtok; free-trial credits). Arm B = Cerebras gemma-4-31b (preview; ~1800 tok/s vendor-rated; reasoning disabled by default; $0.99/$1.49 per Mtok). Verified from Cerebras docs 2026-08-02: these are the only current public-endpoint models besides zai-glm-4.7 (excluded: deprecates 2026-08-17); larger models (Kimi K2.6, GLM 5.1, MiniMax M2.5, Qwen3 32B) exist only on enterprise Dedicated Endpoints and are out of scope. Prompt arms: Handy stock 'Improve Transcriptions' vs stock + correction-marker/cross-sentence extension (known pattern: keep-last-explicit-statement). Baselines: raw Whisper-style synthetic inputs, deterministic-filter-only output.

Decision ledger:
- D1 Cerebras gpt-oss-120b (reasoning_effort=low), free-trial credits — operator direction 2026-08-02.
- D5 smoke-glance-then-live-trial replaces the offline shoot-out as the decision path — operator direction 2026-08-02 ('why build a testing rig for something we can just do and then test').
- D2 synthetic corpus only; no real transcripts — carried from TASK-7 D3.
- D3 corpus includes cross-sentence self-corrections and frustration aborts with zero intent-flip tolerance — operator emphasis 2026-08-02.
- D4 local LLM execution is excluded (operator rejected local latency 2026-08-02); RX 6400 is reserved for ASR acceleration.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Build the synthetic corpus (80-120 items, 8-12 per category incl. cross-sentence corrections, frustration aborts, adversarial prompt-injection, commands/code vocabulary, proper nouns, already-clean bait).
- [ ] #2 Run all candidates x prompt arms with temperature 0 and recorded model IDs; capture outputs, usage tokens, and per-call latency.
- [ ] #3 Score the error taxonomy (over-deletion, addition, meaning change, answered-content, instruction-following, register change, proper-noun/number corruption, leftover disfluency) and semantic-preservation checks (critical-span exact match, length ratio).
- [ ] #4 Report p50/p95 latency per candidate, per-dictation and projected monthly cost, and a ranked pick with the losing evidence shown.
- [ ] #5 Total API spend stays under $1; no Handy source, settings, or runtime touched.
<!-- AC:END -->
