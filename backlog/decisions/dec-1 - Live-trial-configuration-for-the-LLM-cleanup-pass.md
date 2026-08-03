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
- ASR model swapped from Q8_0 to **Q4_K_M**
  (`handy-computer/whisper-large-v3-turbo-gguf`): identical 2.00%
  LibriSpeech WER across all quants in the model card, 40% fewer bytes on a
  bandwidth-bound iGPU. Revert = restore the Q8_0 `selected_model` string.
  Re-evaluate Q4 vs Q8 on the RX 6400 (TASK-9) — the tradeoff shifts when
  bandwidth is plentiful.

## Consequences

Kill switch: `post_process_enabled: false` instantly reverts every dictation
path to raw Whisper output, no rebuild. Stop criteria from the evaluation
plan: any answered-content or intent-flip event disables the trial; p95
added latency over 2s reopens the budget decision.
