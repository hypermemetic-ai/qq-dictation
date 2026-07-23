---
id: doc-1
title: herdr target binding plan
type: specification
created_date: '2026-07-23 15:45'
---

# Plan: bind dictation to the focused herdr pane

## Intended outcome

When the operator starts dictation while a herdr pane is focused, the finished
transcription is delivered to *that* pane — `herdr pane send-text`, then
`send-keys enter` when `auto_submit` is on — no matter where focus has moved
by completion. Fire-and-forget dictation into herdr panels.

## Ownership boundary

- Rust backend only: new `target_binding` module (capture + deliver),
  wired into `TranscribeAction::start` (capture) and the paste call site
  (deliver), plus one setting (`herdr_binding_enabled`, default true) and a
  settings-UI toggle.
- No changes to paste methods, clipboard handling, or non-Linux behavior.

## Non-goals

- Generic X11/Wayland window refocus for non-herdr targets.
- Element-level focus inside arbitrary apps; Wayland/macOS/Windows binding
  (feature no-ops there).
- Changes to `auto_submit` semantics outside the herdr delivery path.

## Mechanism (verified against herdr 0.7.5)

- Capture: on X11, `xdotool getactivewindow getwindowname` == "herdr", then
  `herdr api snapshot` → `focused_pane_id`. Both cheap shell-outs, run on a
  worker thread off the hot start path.
- Deliver: `herdr pane send-text <pane> <text with \n collapsed to spaces>`;
  if `auto_submit`, `herdr pane send-keys <pane> enter`.
- Fallback: any capture/delivery failure → existing focus-based paste.

## Success evidence

- Unit tests: snapshot-JSON → focused pane parsing; newline collapsing;
  fallback decision logic.
- Live check on the operator machine: dictate into pane A, move focus to
  pane B / another app before completion → text lands in A; with
  auto_submit on, A also receives enter. Dictate with focus outside herdr →
  unchanged behavior. Kill the bound pane before completion → fallback paste.
- `cargo test`, `cargo clippy`, frontend type-check green.
