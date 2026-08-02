---
id: TASK-5
title: Retire the custom FDT cleanup pass
status: Done
assignee: []
created_date: '2026-08-01 18:25'
updated_date: '2026-08-02 02:56'
labels: []
dependencies: []
modified_files:
  - README.md
  - docs/local-distribution.md
  - docs/project-concepts.md
  - packaging/handy-ptt-bridge.py
  - scripts/fetch-fdt-model.sh
  - scripts/install-local.sh
  - src-tauri/Cargo.lock
  - src-tauri/Cargo.toml
  - src-tauri/src/audio_toolkit/disfluency.rs
  - src-tauri/src/audio_toolkit/mod.rs
  - src-tauri/src/lib.rs
  - src-tauri/src/managers/transcription.rs
  - src-tauri/src/signal_handle.rs
  - src-tauri/src/target_binding.rs
  - src-tauri/src/transcription_coordinator.rs
  - backlog/tasks/task-5 - Retire-the-custom-FDT-cleanup-pass.md
priority: high
type: chore
ordinal: 5000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Outcome: qq-dictation performs one transcription pass with the selected Handy ASR model. The private distribution continues to own Herdr pane targeting and the Right-Control PTT bridge, but no longer fetches, loads, or runs the FDT disfluency classifier.

Decision ledger:
- D1 retire FDT completely rather than merely disabling it — explicit operator direction in the 2026-08-01 setup conversation.
- D2 preserve qq-dictation for original-Herdr-pane delivery after focus moves — explicit operator clarification in the same conversation.
- D3 use Whisper Large v3 Turbo with Handy post-processing disabled — explicit operator approval in the same conversation.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The FDT runtime, model fetch/install path, and FDT-only dependencies are absent from current source and packaging.
- [x] #2 Herdr pane capture/delivery remains intact, and Right-Control PTT cannot invert when an input arrives while transcription is processing.
- [x] #3 The reproducible local build contains no FDT model and starts without attempting to load one.
- [x] #4 The installed runtime uses Whisper Large v3 Turbo with post-processing disabled.
- [x] #5 Installed UAT produces unprefixed text without raising the desktop panel and remains synchronized across a press during processing.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Remove FDT runtime integration, model fetch packaging, dependencies, and current distribution documentation.
2. Add or update focused checks that prove the remaining transcript cleanup path and preserved Herdr/PTT behavior.
3. Run source checks, build the capped local artifact, install it, and verify the single-pass runtime.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Fresh source checks: Cargo metadata accepted the exact pre-FDT lock with --locked; current runtime/docs/package search contains no FDT, disfluency, or tokenizers references; shell/Python syntax checks pass; target-binding and PTT source are unchanged.

Reproducible Docker build passed under the 5 GiB/2 CPU caps. The 124 MiB AppDir records commit 6257fd9, contains no FDT-named payload, and its binary contains no FDT loader/runtime markers. Focused release tests: 8 target_binding tests passed, 0 failed.

Installed-runtime UAT exposed two delivery UX defects. Saved raw Turbo transcripts contain no leading dashes; target_binding passed a standalone `--` after the pane id and Herdr inserted it literally. Handy logs also show the minimal overlay falling back to a normal bottom-screen window, which raises the Cinnamon panel. Follow-up fix removes the injected argument, adds a regression test, and disables the overlay while preserving tray state.

Runtime UAT then exposed a PTT state-inversion defect. At 02:15:09 Handy ignored a SIGUSR2 press while the first Turbo transcription was still Processing; the bridge still emitted its release as the same toggle at 02:15:11, which started a recording. Later press/release pairs stayed inverted, produced zero-sample recordings, and the final release left Handy recording indefinitely. The fix preserves SIGUSR1/SIGUSR2 toggle compatibility but adds Linux SIGRTMIN press and SIGRTMIN+1 release inputs routed through the coordinator’s existing push-to-talk semantics. A release from Idle or Processing is therefore harmless. The bridge now uses these explicit signals.

Operator clarified that auto-submit and a visible recording indicator are required. These are packaging/runtime concerns, not Handy-binary changes: installation now enforces auto_submit=true, and the Python PTT bridge owns a small override-redirect X11 recording badge while Handy’s compositor-sensitive overlay remains disabled. The bridge and settings were deployed directly and Handy restarted; no AppDir rebuild was performed for these changes.

Final operator UAT passed: the override-redirect recording badge appeared during Right-Control hold, the transcript arrived without a prefix, and auto-submit sent the message on release. The explicit-release path had already remained harmless after the pipeline returned to Idle, proving the prior toggle inversion is gone.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Retired the custom FDT second pass and installed Whisper Large v3 Turbo as the sole ASR pass with API post-processing disabled. Preserved Herdr-pane delivery, corrected the literal `--` prefix, replaced toggle-based Right-Control control with explicit PTT press/release signals, kept Handy’s taskbar-raising overlay disabled, restored a bridge-owned recording badge, and enabled auto-submit. Final operator UAT confirmed the badge and automatic sending.
<!-- SECTION:FINAL_SUMMARY:END -->
