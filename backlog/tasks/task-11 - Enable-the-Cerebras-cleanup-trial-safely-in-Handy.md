---
id: TASK-11
title: Enable the Cerebras cleanup trial safely in Handy
status: To Do
assignee: []
created_date: '2026-08-02 16:58'
labels: []
dependencies: []
priority: high
type: bug
ordinal: 9000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Outcome: Handy's built-in post-processing hook can be enabled for a live Cerebras gpt-oss-120b cleanup trial without two known failure modes: (1) the reqwest client has no timeout, so a stalled provider connection hangs paste+auto-submit indefinitely; (2) a blank cleanup result (observed live: the model returned empty for a prompt-injection corpus item) would overwrite the raw transcript with empty text, which auto-submit then delivers. Both must fail open to the raw transcript.

Decision ledger:
- D1 provider/model/prompt for the trial (Cerebras gpt-oss-120b, reasoning_effort=low, extended prompt) — operator direction 2026-08-02 + Stage 0 live results (0 critical failures, p50 245ms, $0.166 total spend).
- D2 smoke-then-live-trial path — operator direction 2026-08-02 (TASK-8 D5).
- D3 timeout value 3s then fail open to raw — doc-5 §5 evaluation budget, operator-affirmed plan.
- D4 blank cleanup result falls back to raw transcript — reproduced from source (actions.rs:437-441: Some("") overwrites final_text) and observed in live smoke output INJ01.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 reqwest client for post-processing has a bounded total timeout (3s) and a timeout yields the raw transcript.
- [ ] #2 A blank/whitespace cleanup result (structured or legacy path) yields the raw transcript and leaves post_processed_text unset.
- [ ] #3 Focused tests cover the blank-result guard; existing test suite stays green.
- [ ] #4 No behavior change when post-processing is disabled; no settings or installed-runtime changes in this Change.
<!-- AC:END -->
