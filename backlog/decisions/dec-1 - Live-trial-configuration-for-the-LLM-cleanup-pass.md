---
id: DEC-1
title: Live trial configuration for the LLM cleanup pass (2026-08-02)
date: '2026-08-02'
status: accepted
---

## Context

The Stage 0 smoke (384 live Cerebras calls, zero critical failures, p50 245ms)
led the operator to enable a live trial. The configuration lives in the
runtime settings file (`~/.local/share/com.pais.handy/settings_store.json`),
which is deliberately not versioned — it contains the API key and
machine-local state. This record captures the deliberate decisions so the
trial configuration is reproducible without version-controlling secrets.

## Decision

Enabled on 2026-08-02 via direct settings edits (timestamped backups kept
next to the settings file):

- `post_process_enabled: true` — note: `scripts/install-local.sh`
  deliberately resets this to `false` on every install, so it must be
  re-flipped after each reinstall.
- Provider `cerebras`, model `gpt-oss-120b`, key from
  `~/.config/qq-dictation/stage0-keys.env` (mode 600, never committed).
- Prompt `qq_extended_cleanup` = the `extended` arm of
  `experiments/stage0/prompts.json` (the smoke winner).
- `reasoning_effort=low` for Cerebras (source: PR #16), matching the smoke
  configuration.
- PTT routes through post-processing when the setting is on (source: PR #17).
- ASR model: **Parakeet Unified EN 0.6B (Q8_0)** as of 2026-08-02. A bench
  sweep on six real dictations showed it ~5x faster than whisper turbo Q8_0
  with no word-level losses (its verbatim filler retention is absorbed by
  the cleanup pass), and it corrected one real turbo mishearing
  ("merged", not "emerged"). small.en was measured at 2.65x but dropped
  with a real name error ("V3" -> "P3"). Q4_K_M whisper was tried and
  reverted (0.82x — K-quant dequant overhead exceeds RADV bandwidth
  savings). Re-bench on the RX 6400 (TASK-9) with the same harness.
- Prompt: v3, validated in a 19-item two-round trust-envelope experiment
  (2026-08-02). v2's broad "unambiguous signals" generalization was
  REJECTED by evidence: it questionized a declarative trap and gained
  nothing on unlisted markers (resolution variance is the model's, not
  the list's). v3 keeps the enumerated marker list and adds two scoped
  rules: (a) syntax-based question punctuation (auxiliary-before-subject
  word order only — declarative traps stay statements) plus clause-boundary
  comma restoration; (b) a licensing line that a question/request-shaped
  transcript is still content to clean — fixing a discovered production
  failure where such transcripts came back EMPTY (the anti-injection line
  over-fired; the TASK-11 blank guard correctly served raw when this hit
  the operator live). Also: hesitation markers always dropped, stance
  openers protected, colloquial contractions normalized (operator
  preference).
- Ops note: pre-seeding a model into Handy's HF cache requires the real
  repo revision and a refs/main file with NO trailing newline (hf-hub
  resolves refs literally); a fabricated snapshot dir fails is_downloaded.

## Consequences

Kill switch: `post_process_enabled: false` instantly reverts every dictation
path to raw Whisper output, no rebuild. Stop criteria from the evaluation
plan: any answered-content or intent-flip event disables the trial; p95
added latency over 2s reopens the budget decision.
