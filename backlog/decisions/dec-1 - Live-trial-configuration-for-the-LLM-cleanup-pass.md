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
- ASR model: **stays on Q8_0.** A Q4_K_M swap was tried on 2026-08-02 and
  reverted the same day after an on-host A/B (transcribe-bench, Vulkan,
  flash, 6 real dictations 2.7s–53.8s): Q4_K_M was *slower* everywhere
  (0.82x overall, 0.64x on the 53.8s clip). RADV/Vulkan has an optimized
  path for Q8_0's simple int8 blocks, while K-quant dequantization overhead
  exceeds the bandwidth savings on this backend. Word-level output was
  nearly identical (one disagreement in six clips, otherwise punctuation
  cadence only). The vendor WER table (equal WER across quants, measured on
  CUDA/L4) did not predict RADV speed. Re-test on the RX 6400 (TASK-9),
  where bandwidth is plentiful and the tradeoff may flip; the Q4_K_M file
  remains in the HF cache, and the bench harness used for the A/B is the
  same one TASK-9 will use.

## Consequences

Kill switch: `post_process_enabled: false` instantly reverts every dictation
path to raw Whisper output, no rebuild. Stop criteria from the evaluation
plan: any answered-content or intent-flip event disables the trial; p95
added latency over 2s reopens the budget decision.
