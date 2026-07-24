---
id: doc-3
title: Herdr binding reliability plan
type: specification
created_date: '2026-07-24 18:38'
updated_date: '2026-07-24 20:04'
tags:
  - plan
  - herdr
  - dictation
  - safety
  - build
---
# Plan: make Herdr-bound dictation reliable and fail closed

## Intended outcome

Dictation started in a Herdr pane remains bound to that pane after desktop-session restart, regardless of later focus. If the target cannot be captured or delivered, Handy never redirects the transcript as simulated keyboard input into another tab or application. The reproducible local build used to deliver the repair cannot exhaust the workstation and collapse the operator's desktop.

## Ownership boundary

- Rust `target_binding` capture/delivery state and its paste integration.
- Runtime resolution of the local Herdr executable.
- `scripts/build-local.sh` native build concurrency and Docker resource containment.
- Focused regression checks and local package verification.

## Non-goals

- Generic window or element binding outside Herdr.
- Wayland, macOS, or Windows target binding.
- Changes to Herdr itself or to legacy delivery when recording starts outside Herdr.
- General workstation resource management or unrelated service cleanup.

## Approved decisions

1. Resolve `herdr` from the process PATH first, then the standard Linuxbrew path `/home/linuxbrew/.linuxbrew/bin/herdr` used by this machine.
2. Preserve distinct outcomes for legacy/non-Herdr delivery, a bound Herdr pane, and Herdr targeting failure.
3. Once a recording is identified as Herdr-targeted, capture timeout, missing CLI, closed pane, and delivery failure fail closed: emit the existing paste-error path, retain the transcript in Handy history, and send no OS-level keyboard input.
4. Starts outside Herdr and an explicitly disabled binding setting retain current focus-based delivery.
5. After two confirmed global OOM events from parallel native release compilation, permanently serialize Cargo and CMake to one job and cap the Docker build at 5 GiB total memory (no container swap) and 2 CPUs. A cap breach must fail the build locally rather than permit global OOM.

Dispositions: decisions 1–4 were approved in the 2026-07-24 safe-fix alignment exchange. Decision 5 and the build-script boundary expansion were approved in the 2026-07-24 OOM realignment exchange after the operator received the kernel/resource diagnosis and selected the recommended 5 GiB / 2 CPU limits.

## Success evidence

- A regression check reproduces the desktop-session PATH without Linuxbrew and verifies Herdr resolution.
- Tests distinguish non-Herdr, bound, and failed capture states and prove failed Herdr delivery cannot reach the legacy keyboard path.
- Applicable Rust checks pass.
- The capped, serialized local build completes without global OOM and records the exact Change commit.
- A rebuilt local install is restarted with the desktop-session environment; pane-switch delivery lands in the starting pane, while a closed target produces no cross-OS keystrokes.
- Fresh-context review finds no material introduced failure.
