---
id: doc-3
title: Herdr binding reliability plan
type: specification
created_date: '2026-07-24 18:38'
updated_date: '2026-07-24 18:39'
tags:
  - plan
  - herdr
  - dictation
  - safety
---
# Plan: make Herdr-bound dictation reliable and fail closed

## Intended outcome

Dictation started in a Herdr pane remains bound to that pane after desktop-session restart, regardless of later focus. If the target cannot be captured or delivered, Handy never redirects the transcript as simulated keyboard input into another tab or application.

## Ownership boundary

- Rust `target_binding` capture/delivery state and its paste integration.
- Runtime resolution of the local Herdr executable.
- Focused regression checks and local package verification.

## Non-goals

- Generic window or element binding outside Herdr.
- Wayland, macOS, or Windows target binding.
- Changes to Herdr itself or to legacy delivery when recording starts outside Herdr.

## Approved decisions

1. Resolve `herdr` from the process PATH first, then the standard Linuxbrew path `/home/linuxbrew/.linuxbrew/bin/herdr` used by this machine.
2. Preserve distinct outcomes for legacy/non-Herdr delivery, a bound Herdr pane, and Herdr targeting failure.
3. Once a recording is identified as Herdr-targeted, capture timeout, missing CLI, closed pane, and delivery failure fail closed: emit the existing paste-error path, retain the transcript in Handy history, and send no OS-level keyboard input.
4. Starts outside Herdr and an explicitly disabled binding setting retain current focus-based delivery.

Disposition: operator approved the recommended safe-fix plan in the 2026-07-24 asked-and-answered alignment exchange.

## Success evidence

- A regression check reproduces the desktop-session PATH without Linuxbrew and verifies Herdr resolution.
- Tests distinguish non-Herdr, bound, and failed capture states and prove failed Herdr delivery cannot reach the legacy keyboard path.
- Applicable Rust checks pass.
- A rebuilt local install is restarted with the desktop-session environment; pane-switch delivery lands in the starting pane, while a closed target produces no cross-OS keystrokes.
- Fresh-context review finds no material introduced failure.
