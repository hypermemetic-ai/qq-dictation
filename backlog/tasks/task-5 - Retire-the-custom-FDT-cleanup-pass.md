---
id: TASK-5
title: Retire the custom FDT cleanup pass
status: In Progress
assignee: []
created_date: '2026-08-01 18:25'
updated_date: '2026-08-01 18:30'
labels: []
dependencies: []
modified_files:
  - README.md
  - docs/local-distribution.md
  - docs/project-concepts.md
  - scripts/fetch-fdt-model.sh
  - scripts/install-local.sh
  - src-tauri/Cargo.lock
  - src-tauri/Cargo.toml
  - src-tauri/src/audio_toolkit/disfluency.rs
  - src-tauri/src/audio_toolkit/mod.rs
  - src-tauri/src/managers/transcription.rs
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
- [x] #2 Herdr pane capture/delivery and the Right-Control PTT bridge remain unchanged and covered by focused checks.
- [ ] #3 The reproducible local build contains no FDT model and starts without attempting to load one.
- [ ] #4 The installed runtime uses Whisper Large v3 Turbo with post-processing disabled.
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
<!-- SECTION:NOTES:END -->
