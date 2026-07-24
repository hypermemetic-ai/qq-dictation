---
id: TASK-3
title: Make Herdr-bound dictation reliable and fail closed
status: In Progress
assignee: []
created_date: '2026-07-24 18:39'
updated_date: '2026-07-24 19:23'
labels: []
dependencies: []
documentation:
  - doc-3
modified_files:
  - src-tauri/src/target_binding.rs
  - src-tauri/src/clipboard.rs
priority: high
type: bug
ordinal: 3000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
After a desktop login restart, qq-dictation inherits a PATH that omits Linuxbrew. Target capture cannot spawn `herdr`, silently reports the recording as non-Herdr, and falls back to character-by-character focus typing. A long transcription can therefore follow focus into another tab and continue injecting keystrokes across the operating system after that tab closes.

Outcome: restore pane binding under the desktop-session environment and make every identified Herdr-targeting failure refuse OS-level keyboard fallback. Preserve current behavior for recordings genuinely started outside Herdr or with binding explicitly disabled.

## Decision ledger

- D1 executable resolution: use process PATH, then `/home/linuxbrew/.linuxbrew/bin/herdr` — disposition: operator approval of the recommended safe-fix plan in asked-and-answered alignment exchange 2026-07-24.
- D2 Herdr failure semantics: targeting/capture/delivery failure sends no simulated keyboard input, emits the existing paste-error path, and leaves the saved history entry available — disposition: same exchange.
- D3 compatibility boundary: non-Herdr starts and explicitly disabled binding retain legacy focus-based delivery; no generic app binding — disposition: same exchange.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 With the desktop-session PATH that omits Linuxbrew, recording started in Herdr captures and delivers to its starting pane
- [ ] #2 Switching focus after recording starts cannot redirect a Herdr-bound transcript
- [ ] #3 Missing Herdr CLI, capture timeout, closed pane, or delivery failure after a Herdr start emits no OS-level keyboard input and surfaces paste-error while history retains the transcript
- [ ] #4 Recording started outside Herdr or with binding disabled preserves existing focus-based delivery
- [ ] #5 Automated regression checks and fresh local acceptance checks cover the repaired and fail-closed paths
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add an observable regression around the sanitized desktop-session PATH and explicit capture outcomes.
2. Resolve the Herdr executable through PATH plus the standard Linuxbrew location.
3. Carry legacy, bound-pane, and targeting-failure states through capture; fail closed before keyboard fallback for Herdr failures.
4. Run focused and applicable Rust checks, then fresh-context review.
5. Build and install the branch, restart it under the desktop-session environment, and verify pane-switch plus closed-pane behavior before finalizing the Task and pull request.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implementation delegate committed `caf4609` (explicit Legacy/Bound/Failed capture outcomes, PATH + Linuxbrew Herdr resolution, and fail-closed paste integration). Its process ended after structured completion because the generic child requested an unavailable supervisor tool; the structured envelope was recovered and every tree claim was verified by the accountable owner.

The required same-fix-smaller pass replaced a redundant delivery enum with `Result<Option<String>, String>` in `0896c6d`, removing 12 net lines while preserving policy tests.

Owner Checks: primary Rust LSP clean for both changed files; `cargo fmt --check` and `git diff --check` pass; pinned Docker focused suites pass (8 target-binding tests and 5 clipboard tests); full library suite is 145 pass, 1 pre-existing catalog `moss` failure, 2 ignored; the unchanged base exhibits the same catalog failure; advisory clippy completes with six pre-existing warnings and strict `-D warnings` is blocked by those baseline warnings.
<!-- SECTION:NOTES:END -->
