---
id: TASK-2
title: Bind dictation to the focused herdr pane
status: In Progress
assignee: []
created_date: '2026-07-23 15:43'
updated_date: '2026-07-23 17:39'
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
