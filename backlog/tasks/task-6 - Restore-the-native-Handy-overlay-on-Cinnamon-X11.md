---
id: TASK-6
title: Restore the native Handy overlay on Cinnamon X11
status: In Progress
assignee: []
created_date: '2026-08-02 03:09'
updated_date: '2026-08-02 03:32'
labels: []
dependencies: []
modified_files:
  - README.md
  - docs/local-distribution.md
  - packaging/handy-ptt-bridge.py
  - scripts/build-local.sh
  - scripts/install-local.sh
  - src-tauri/src/overlay.rs
  - src-tauri/src/target_binding.rs
priority: high
type: bug
ordinal: 6000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Outcome: Right-Control dictation uses Handy's original recording/transcribing overlay. The temporary Python/X11 badge is removed. Diagnose any Cinnamon panel activation from direct evidence instead of treating it as the overlay's intended behavior.

Decision ledger:
- Restore Handy's original overlay and remove the bridge-owned badge — operator requested the original overlay and rejected the custom indicator in this conversation.
- Preserve auto-submit and explicit push-to-talk delivery — operator requested the former behavior and approved proceeding in this conversation.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The bridge-owned recording indicator is absent from source and the deployed bridge.
- [x] #2 Handy native overlay is enabled and visibly reports recording and transcribing.
- [x] #3 Right-Control dictation does not open or raise the Cinnamon panel.
- [x] #4 Auto-submit and explicit PTT press/release behavior remain working.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Restore the native minimal overlay and remove the temporary bridge badge without rebuilding Handy.
2. Reproduce under Cinnamon/X11 while inspecting the native overlay window and panel state.
3. If panel activation recurs, isolate the window-manager interaction and fix the smallest responsible layer.
4. Run operator UAT and land through GitHub Flow.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Fresh Cinnamon/X11 evidence: the active Ghostty Herdr window was fullscreen. Handy's hidden native overlay was a non-focusable NORMAL window with no EWMH state, despite the Tauri builder flags. During an operator-confirmed recording, mapping that window raised the bottom panel. A reversible live probe changed only the overlay type to NOTIFICATION; Cinnamon then supplied ABOVE, SKIP_TASKBAR, and SKIP_PAGER. During the next real dictation, the overlay was viewable as NOTIFICATION, Ghostty remained FULLSCREEN and FOCUSED, and a before/during comparison of the bottom 40-pixel strip changed zero pixels. This reproduces the failure and validates the smallest native fix before rebuilding. Host cargo is intentionally absent; the pinned container build is the compile Check.

The first container invocation stopped before compilation because this Change worktree shares cargo, target, and ORT caches through host symlinks whose targets were outside the /work mount. scripts/build-local.sh now detects the shared cargo symlink and mounts its resolved cache root over /work/.docker-cache. This preserves the cache and makes the documented build command work from methodology-required Change worktrees.

Post-install Check against built commit 76fe322: the rebuilt overlay had a new X11 window ID and was viewable during an actual Right-Control recording as NET_WM_WINDOW_TYPE_NOTIFICATION with SKIP_PAGER, SKIP_TASKBAR, and ABOVE. The active Ghostty window remained FULLSCREEN and FOCUSED; a bottom-strip capture showed only fullscreen terminal content and no Cinnamon panel. The bridge then logged the matching release and transcription request. The installer retained the previous AppDir at Handy.AppDir.backup.20260802T032949Z. A stale installer completion sentence that mentioned only the tray was corrected afterward as a packaging-only change; it does not require another native build.

The repository-wide cargo fmt Check also exposed a pre-existing format-only deviation in src-tauri/src/target_binding.rs on origin/main. Because code-quality CI runs the full formatter, this Change applies rustfmt's single-line normalization. It has no semantic or installed-binary effect and therefore does not justify rebuilding Handy.

Prettier likewise normalized the existing README signal table introduced with explicit PTT. This is formatting-only and closes the remaining code-quality gate without changing runtime behavior.
<!-- SECTION:NOTES:END -->
