---
id: TASK-3
title: Make Herdr-bound dictation reliable and fail closed
status: Done
assignee: []
created_date: '2026-07-24 18:39'
updated_date: '2026-07-24 20:31'
labels: []
dependencies: []
documentation:
  - doc-3
modified_files:
  - src-tauri/src/target_binding.rs
  - src-tauri/src/clipboard.rs
  - scripts/build-local.sh
priority: high
type: bug
ordinal: 3000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
After a desktop login restart, qq-dictation inherits a PATH that omits Linuxbrew. Target capture cannot spawn `herdr`, silently reports the recording as non-Herdr, and falls back to character-by-character focus typing. A long transcription can therefore follow focus into another tab and continue injecting keystrokes across the operating system after that tab closes.

While building the reviewed repair, the repository's uncapped parallel native release build caused two confirmed global OOM events. Two concurrent `cc1plus` processes held roughly 5.3–6.1 GiB RSS while other interactive work exhausted 16.47 GiB RAM plus 2.15 GiB swap; Linux killed Chromium and Ghostty/Herdr collapsed. The build must be contained before it can safely deliver the runtime repair.

Outcome: restore pane binding under the desktop-session environment; make every identified Herdr-targeting failure refuse OS-level keyboard fallback; and make the local release build serialize native compilation inside a hard container resource budget. Preserve current behavior for recordings genuinely started outside Herdr or with binding explicitly disabled.

## Decision ledger

- D1 executable resolution: use process PATH, then `/home/linuxbrew/.linuxbrew/bin/herdr` — disposition: operator approval of the recommended safe-fix plan in asked-and-answered alignment exchange 2026-07-24.
- D2 Herdr failure semantics: targeting/capture/delivery failure sends no simulated keyboard input, emits the existing paste-error path, and leaves the saved history entry available — disposition: same exchange.
- D3 compatibility boundary: non-Herdr starts and explicitly disabled binding retain legacy focus-based delivery; no generic app binding — disposition: same exchange.
- D4 build containment and expanded boundary: serialize Cargo and CMake to one job; cap Docker at 5 GiB total memory with no container swap and 2 CPUs; include `scripts/build-local.sh` in TASK-3 rather than a separate Change — disposition: operator approval in asked-and-answered OOM realignment exchanges 2026-07-24.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 With the desktop-session PATH that omits Linuxbrew, recording started in Herdr captures and delivers to its starting pane
- [x] #2 Switching focus after recording starts cannot redirect a Herdr-bound transcript
- [x] #3 Missing Herdr CLI, capture timeout, closed pane, or delivery failure after a Herdr start emits no OS-level keyboard input and surfaces paste-error while history retains the transcript
- [x] #4 Recording started outside Herdr or with binding disabled preserves existing focus-based delivery
- [x] #5 Automated regression checks and fresh local acceptance checks cover the repaired and fail-closed paths
- [x] #6 The local release build serializes Cargo and CMake inside a Docker hard limit of 5 GiB total memory with no container swap and 2 CPUs, failing locally rather than causing global OOM
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add an observable regression around the sanitized desktop-session PATH and explicit capture outcomes.
2. Resolve the Herdr executable through PATH plus the standard Linuxbrew location.
3. Carry legacy, bound-pane, and targeting-failure states through capture; fail closed before keyboard fallback for Herdr failures.
4. Serialize Cargo/CMake in `scripts/build-local.sh` and hard-cap its Docker run at 5 GiB total memory, no container swap, and 2 CPUs.
5. Run focused and applicable Rust/build checks, then fresh-context review of every implementation and review-fix delta.
6. Build and install the branch under the new cap, restart it with the desktop-session environment, and verify pane-switch plus closed-pane behavior before finalizing the Task and pull request.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implementation delegate committed `caf4609` (explicit Legacy/Bound/Failed capture outcomes, PATH + Linuxbrew Herdr resolution, and fail-closed paste integration). Its process ended after structured completion because the generic child requested an unavailable supervisor tool; the structured envelope was recovered and every tree claim was verified by the accountable owner.

The required same-fix-smaller pass replaced a redundant delivery enum with `Result<Option<String>, String>` in `0896c6d`, removing 12 net lines while preserving policy tests.

Owner Checks: primary Rust LSP clean for both changed files; `cargo fmt --check` and `git diff --check` pass; pinned Docker focused suites pass (8 target-binding tests and 5 clipboard tests); full library suite is 145 pass, 1 pre-existing catalog `moss` failure, 2 ignored; the unchanged base exhibits the same catalog failure; advisory clippy completes with six pre-existing warnings and strict `-D warnings` is blocked by those baseline warnings.

Fresh-context review run `a2789263` found one medium blocker: PasteMethod::None suppressed an identified targeting failure instead of emitting paste-error. Reproduced from the pure policy helper and fixed in `b191761`, which removed five net lines and made `CaptureOutcome::Failed` precede the configured no-op. Pinned Docker clipboard tests remained 5/5. Independent fresh fix-delta review `a199de74` passed with no remaining blocker.

Scope realigned after two kernel-confirmed global OOM events during the required local release build (14:01:29 and 14:46:54 CDT). The operator explicitly chose to expand TASK-3 and approved permanent defaults: one Cargo/CMake job, 5 GiB Docker memory with no container swap, and 2 CPUs. The unconstrained build was interrupted; no qq-dictation build container or build process remained active when checked.

Capped-build evidence: live Docker inspect recorded Memory=5368709120, MemorySwap=5368709120, NanoCpus=2000000000, CARGO_BUILD_JOBS=1, and CMAKE_BUILD_PARALLEL_LEVEL=1. The release build completed in 7m58s, produced an AppDir marked `63806d2`, and generated no new kernel OOM lines. Fresh-context build-containment review `7dd67d07` passed with no material finding. The earlier uncapped run is not accepted and no builder remains active.

Installed acceptance: branch artifact `63806d2` installed successfully, then Handy was killed and relaunched directly under the Cinnamon desktop PATH (which does not resolve `herdr`). Four subsequent recordings bound and delivered directly to Herdr panes. Operator UAT dictated in pane `w1Z:p1` and switched tabs immediately; the text returned and submitted in `w1Z:p1`, with matching bind/deliver logs. The operator explicitly accepted the result and skipped the disposable closed-pane hands-on check; the closed/missing/failing target behavior remains covered by focused policy tests and two fresh-context reviews.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Restored reliable Herdr pane binding after desktop login by resolving the CLI from PATH or the standard Linuxbrew location and carrying explicit Legacy/Bound/Failed capture outcomes. Identified Herdr capture or delivery failures now return through paste-error before any OS keyboard path, while genuine non-Herdr/disabled starts retain legacy behavior. After two global OOM events during delivery, the approved scope expanded to serialize Cargo/CMake and cap local Docker builds at 5 GiB total memory, no container swap, and 2 CPUs.

Evidence: primary Rust LSP and formatting clean; 8/8 target-binding and 5/5 clipboard focused tests pass; full library suite 145 pass with one unchanged catalog failure and two ignored; three fresh-context review passes after one repaired finding; capped release build completed with live cgroup inspection and no new OOM; installed artifact `63806d2` ran under the sanitized desktop PATH; operator pane-switch UAT bound and delivered to the original pane and was explicitly accepted.
<!-- SECTION:FINAL_SUMMARY:END -->
