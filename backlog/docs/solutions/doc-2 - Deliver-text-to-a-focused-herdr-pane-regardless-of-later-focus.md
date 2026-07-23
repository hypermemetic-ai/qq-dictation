---
id: doc-2
title: Deliver text to a focused herdr pane regardless of later focus
type: guide
created_date: '2026-07-23 19:40'
updated_date: '2026-07-23 19:40'
tags:
  - solution
  - herdr
  - dictation
  - focus
  - delivery
---
# Deliver text to a focused herdr pane regardless of later focus

## Symptom

Asynchronous text delivery (dictation transcription completing seconds after
recording stops) lands wherever keyboard focus happens to be at completion
time. When the operator moves to another herdr pane — or another app — while
waiting, the text follows focus and lands in the wrong place.

## Root cause

Herdr panes are invisible to the OS: an entire herdr workspace lives inside a
single terminal emulator window, so no window-level mechanism (xdotool
activation, accessibility focus) can distinguish panes, let alone deliver to
one. Keystroke simulation (enigo/xdotool/wtype) inherently targets the focus
of the moment, so any async text producer races the operator's own navigation.

Herdr's server, however, tracks pane focus itself and exposes it over its
socket API, and can write directly to a pane's PTY — bypassing OS focus
entirely.

## Resolution

Bind the target at production start, deliver through herdr's API at completion:

- At start, require the active X11 window to be herdr's (its client sets the
  window title to `herdr`), then read `herdr api snapshot` →
  `result.snapshot.focused_pane_id`. Do not use `herdr pane current` — it
  reports the pane the calling process runs in, not the focused pane.
- Key bindings by a per-production token (a map, not a single slot): a new
  production can legally start while a previous result is still in flight, and
  a shared slot cross-delivers the older result to the newer target.
- Deliver with `herdr pane send-text <pane> -- <text>` (the `--` guards
  leading-dash text from clap) and submit with
  `herdr pane send-keys <pane> enter`. Both are focus-independent and
  race-free.
- Collapse newlines to spaces first: a raw PTY write is not bracketed paste,
  so a literal newline acts as Enter and submits a partial message.
- Bound every subprocess call with a timeout that also kills the child, and
  fall back to focus-based delivery on any failure — a wedged herdr server
  must degrade, never stall.

## Verification

Verified live against herdr 0.7.5 in a scratch workspace: `api snapshot`
focus tracking, `send-text` (with and without `--`), and `send-keys enter`.
Landed in qq-dictation PR #2 (TASK-2): operator UAT delivered dictation to
the bound pane after switching panes mid-flight, with auto-submit, log-
verified; non-herdr targets kept legacy behavior.
