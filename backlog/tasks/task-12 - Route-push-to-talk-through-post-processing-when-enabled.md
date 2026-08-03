---
id: TASK-12
title: Route push-to-talk through post-processing when enabled
status: Done
assignee: []
created_date: '2026-08-03 00:46'
updated_date: '2026-08-03 01:26'
labels: []
dependencies: []
priority: high
type: bug
ordinal: 10000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Outcome: holding right-Control (the PTT bridge) uses the LLM cleanup pass whenever post_process_enabled is true, and raw transcription when false, so the settings toggle is the single switch for every dictation path.

Reproduced from source: signal_handle.rs send_ptt_input hardcodes send_input("transcribe", ...) — PTT bypassed post-processing regardless of the setting (discovered 2026-08-02 after the trial enablement: the hook was armed but the operator's actual dictation path never engaged it). SIGUSR1/SIGUSR2 already route to post-process/raw respectively; PTT was the odd path out.

Decision ledger:
- D1 action selection honors post_process_enabled at send time (settings are re-read per press, so the kill switch is instant with no rebuild) — owner judgment, matches the evident SIGUSR1/2 design.
- D2 trial configuration (Cerebras gpt-oss-120b, reasoning_effort=low, extended prompt) — TASK-8/TASK-11 ledger, unchanged.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 With post_process_enabled=true, a PTT hold-to-talk dictation invokes post_process_transcription (log shows the LLM request lines).
- [ ] #2 With post_process_enabled=false, PTT behaves exactly as before (raw).
- [ ] #3 Focused test covers the action selection; suite stays green.
<!-- AC:END -->
