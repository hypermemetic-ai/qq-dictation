---
id: TASK-2
title: Bind dictation to the focused herdr pane
status: Done
assignee: []
created_date: '2026-07-23 15:43'
updated_date: '2026-07-23 18:46'
labels: []
dependencies: []
documentation:
  - doc-1
modified_files:
  - src/stores/settingsStore.ts
ordinal: 2000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Operator pain: after stopping a long dictation, transcription text lands wherever keyboard focus happens to be at completion — wrong herdr pane, or once a browser URL bar. Herdr panes are invisible to the OS (one Ghostty window), so only the herdr socket API can target them.

Intent: bind the transcription target at recording start (the focused herdr pane) and deliver via `herdr pane send-text` (+ `send-keys enter` when auto_submit is on), so text goes to the right pane regardless of what the operator does after stopping. Fire-and-forget dictation.

## Decision ledger

- D1 scope: herdr-pane binding only; no generic X11 window refocus for other apps — disposition: operator approval in alignment exchange 2026-07-23 (asked-and-answered).
- D2 delivery: `herdr pane send-text` + `send-keys enter` (verified live against herdr 0.7.5 in scratch workspace w4H); auto-submit governed by the existing `auto_submit` setting — disposition: same exchange.
- D3 fallback: if no binding was captured (non-herdr focus, unsupported platform) or delivery fails (pane closed, CLI error), fall back to the existing focus-based paste — disposition: same exchange.
- D4 newlines collapsed to spaces on the herdr path (raw PTY write; a literal newline would submit early) — disposition: same exchange.
- D5 feature gated by a new setting, default on in this fork — disposition: same exchange.
- D6 delivery base: branch off task/local-fdt-cleanup (the operator's packaging line and only active development line; main mirrors upstream) — disposition: same exchange; revisit at PR time if the operator prefers main.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Recording started while a herdr pane is focused delivers the transcription to that pane even after focus moves to another pane or app
- [ ] #2 With auto_submit enabled, the bound pane receives enter after the text (fire-and-forget)
- [ ] #3 Recording started with focus outside herdr behaves exactly as today
- [ ] #4 If the bound pane is gone or send-text fails, the text falls back to the existing paste path
- [ ] #5 Multi-line post-processed text on the herdr path cannot submit early (newlines collapsed)
- [ ] #6 Setting can disable herdr binding; default on
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Delivered on branch task/herdr-target-binding (commits a7fc733, 92cb113 + finalization).

Mechanism: at recording start (only after successful start), capture the focused herdr pane via `herdr api snapshot` when the active X11 window is herdr's; deliver at paste time via `herdr pane send-text` (+ `send-keys enter` when auto_submit), keyed by per-recording tokens so overlapping in-flight transcriptions can't cross-deliver. Fallback to the legacy focus paste on any failure. New "Bind to Herdr Pane" setting, default on.

Checks: cargo test 140 passed / 1 pre-existing catalog failure (base commit e8c73ba); clippy/tsc/eslint/prettier clean. Three fresh-context review rounds: round 1 found a wrong-pane blocker (global binding slot) — fixed with per-recording tokens; rounds 2–3 verified the fix and follow-ups. Built + installed via scripts/build-local.sh + install-local.sh (AppDir commit 92cb113).

UAT (operator, accepted): (1) dictated into herdr pane w4G:p2, switched away mid-flight — text delivered to w4G:p2 (log-verified); (2) auto_submit=enter — text landed and submitted itself (operator-confirmed); (3) dictate into non-herdr app — legacy behavior unchanged.
<!-- SECTION:FINAL_SUMMARY:END -->
