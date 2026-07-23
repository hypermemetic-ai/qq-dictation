---
id: TASK-1
title: package local Handy with FDT cleanup and recording indicator
status: In Progress
assignee:
  - '@codex'
created_date: '2026-07-23 04:46'
updated_date: '2026-07-23 05:56'
labels: []
dependencies: []
documentation:
  - docs/local-distribution.md
  - docs/project-concepts.md
modified_files:
  - .gitignore
  - .prettierignore
  - AGENTS.md
  - CLAUDE.md
  - CONCEPTS.md
  - README.md
  - backlog/config.yml
  - >-
    backlog/tasks/task-1 -
    package-local-Handy-with-FDT-cleanup-and-recording-indicator.md
  - docs/local-distribution.md
  - docs/project-concepts.md
  - docs/upstream-agent-guide.md
  - packaging/AppRun
  - packaging/Dockerfile
  - packaging/handy
  - packaging/handy-ptt-bridge.py
  - packaging/handy-ptt.service
  - scripts/build-local.sh
  - scripts/fetch-fdt-model.sh
  - scripts/install-local.sh
  - src-tauri/Cargo.lock
  - src-tauri/Cargo.toml
  - src-tauri/src/audio_toolkit/disfluency.rs
  - src-tauri/src/audio_toolkit/mod.rs
  - src-tauri/src/managers/transcription.rs
priority: high
type: feature
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Create qq-dictation as the private, QQ-governed Repository for the operator’s local Handy distribution. Integrate the pinned FDT Mini deletion tagger into the shared post-ASR path, package and install it reproducibly, preserve Handy’s fail-open behavior, and make recording state visible independently of the foreground terminal application.

Decision ledger:
- Private named Repository and local-only packaging: operator request in the 2026-07-22 alignment exchange.
- FDT Mini, atomic span decisions, 128-token maximum-context windows, and user-specific prospective calibration: operator-approved research direction in the preceding exchange.
- Application-independent recording indicator: operator preference in the 2026-07-22 alignment exchange; native Handy minimal overlay selected as the smallest verified implementation.
- Independent private mirror with upstream remote and uncommitted model weights: necessary implementation of the operator’s private-project and local-use constraints; announced before mutation.
- Project name qq-dictation and qqp-dev ownership: reversible repository metadata selected from the machine’s canonical Git identity and announced before creation.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Repository is private, has origin and upstream remotes, and inherits live QQ AGENTS.md and CONCEPTS.md surfaces
- [x] #2 Pinned FDT artifacts are fetched and hash-verified outside Git; model weights are never committed
- [x] #3 English transcripts use first-WordPiece labels, 128-token windows with 32-token overlap and maximum-context merging without truncating the tail
- [x] #4 Deletion runs plus comma/capitalization repairs are accepted or rejected atomically with a configurable span policy and deletion-only reconstruction invariants
- [x] #5 Classifier success precedes custom-word correction and replaces legacy unconditional filler cleanup; every classifier failure follows the untouched legacy path
- [x] #6 The resident cleaner adds negligible warm latency, logs decisions locally, and remains independent of ASR model unloading
- [x] #7 A visible non-focus-stealing recording indicator works above Ghostty/herdr/Pi and Codex through the native Handy minimal overlay
- [ ] #8 The local package installs reproducibly, preserves current Handy settings/models/PTT bridge, and passes focused plus end-to-end Checks
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Establish the v0.9.4 local baseline, QQ surfaces, Task, and project documentation.
2. Implement and unit-test the FDT runtime, 128/32 maximum-context merge, structural action decoder, atomic span policy, and reconstruction invariants.
3. Integrate the resident cleaner into Handy’s shared post-processing path with model fetching, logging, and fail-open legacy fallback.
4. Package, install, and exercise the local Linux build while preserving current ASR models and settings.
5. Verify the native recording overlay, run applicable Checks and fresh-context review, then publish the Change as a private PR.
<!-- SECTION:PLAN:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @codex
created: 2026-07-23 04:49
---
Baseline correction: retain exact Handy upstream commit 8a362e9eba59d4057fda79b7f38f5b0d5cbabf65 rather than rewriting the private main branch to tag v0.9.4. This post-v0.9.4 baseline contains useful recent Linux overlay, clipboard, and audio fixes; the exact commit is documented for reproducibility.
---

author: @codex
created: 2026-07-23 05:39
---
Implementation checkpoint:
- Private origin qqp-dev/qq-dictation and upstream cjpais/Handy are configured; live QQ AGENTS.md and CONCEPTS.md links are present.
- Pinned FDT revision 677a8a0c20f23858e3c581977111a572999ee487 is installed outside Git with all three SHA-256 checks verified.
- Rust classifier contract, 128/32 maximum-context windows, atomic transaction decoder, resident lifecycle, English language gate, custom-word ordering, and exact legacy fallback are implemented.
- Native minimal overlay was exercised above Ghostty/herdr without focus theft.
- Checks: frontend lint/format pass; 9/9 focused classifier tests pass including the real model and long transcript tail; legacy fallback integration test passes. Full library baseline is 134 pass, 1 unrelated upstream catalog failure (new moss architecture absent from KNOWN_ARCHES), 2 ignored.
---

author: @codex
created: 2026-07-23 05:47
---
Installer live-run finding: the first install stopped before service restart because its Xlib preflight used PATH-selected Linuxbrew Python 3.14, while the tracked systemd unit correctly uses /usr/bin/python3 3.12 where python3-xlib is installed. Moved the preflight before mutations and made both installer Python calls explicit to /usr/bin/python3. The staged AppDir, model, wrapper, settings backup, and original before-qq-dictation backups remained intact; service/application restart is pending the corrected rerun.
---

author: @codex
created: 2026-07-23 05:56
---
Live installation hardening and evidence:
- Corrected installer now starts Handy in a detached session and verifies /proc/<pid>/exe resolves to the installed AppDir.
- Pinned artifacts short-circuit locally after SHA-256 verification (14 ms in the live check) instead of redownloading.
- Installer disables the stock upstream update check so the tracked private build remains authoritative; selected Parakeet Q8, English, never-unload, custom words, history, and PTT state remain preserved.
- New binary loaded FDT and both Vulkan0/CPU backends. A SIGUSR2 record/stop cycle completed through Parakeet and FDT. The Recording window appeared while the active Ghostty window ID stayed 62914564 before, during, and after.
- systemd service is active under /usr/bin/python3 and the app autostart entry resolves to the qq-dictation AppDir. Original pre-install settings are recoverable at settings_store.json.before-qq-dictation.20260723T055301Z.
---
<!-- COMMENTS:END -->
