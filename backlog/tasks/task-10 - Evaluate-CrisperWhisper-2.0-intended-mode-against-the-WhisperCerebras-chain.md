---
id: TASK-10
title: Run CrisperWhisper 2.0 as a system-wide PTT sidecar
status: Done
assignee:
  - '@pi-019fc30c'
created_date: '2026-08-02 14:41'
updated_date: '2026-08-02 18:15'
labels: []
dependencies: []
documentation:
  - doc-7
  - doc-8
priority: low
type: spike
ordinal: 8000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Outcome: a contained, reversible working proof of CrisperWhisper 2.0 turbo intended mode on CT2 CPU/INT8 as a system-wide X11 hold-to-talk sidecar that delivers to the Herdr pane captured at recording start. Handy remains installed and unchanged.

Constraints: evaluation-only standard model; no Pro model or purchase; no access to operator recordings, transcript history, Handy logs, secrets, or `~/.local/share/com.pais.handy/`; no changes to Handy, its settings, or its running PTT bridge; synthetic or explicitly non-private audio only; one contained runtime/cache root with documented removal. The Nyra model license expressly covers evaluation but may exclude ongoing operational deployment, so adoption beyond the trial requires clarification.

Decision ledger:
- D1 working local setup is authorized by the operator's 2026-08-02 direction: “I want to try it. I don't care that Handy can't load it. Let's figure out how we would set it up and actually use it.” Downloads and contained pip installs were approved in the same assignment.
- D2 Whisper+Cerebras comparison is OUT OF SCOPE per operator 2026-08-02: “don't even worry about the Whisper Cerebra stuff… I'll run my own A-B test.” TASK-10 no longer depends on TASK-8 results.
- D3 integration is a system-wide PTT sidecar, not Pi Voice STT or Handy support, per operator 2026-08-02: “I want push to talk… let's just pursue that route.”
- D4 the initial 780M-versus-CPU trial was settled by the operator's 2026-08-02 selection of “Benchmark 780M and CPU.” After repeated ROCm inference aborted with native HIP launch failures and destabilized the desktop, the operator realigned: “roll back everything you've done with the GPU… We'll stick to CPU for now. We'll reconsider GPU when the dedicated one comes in.” GPU state and temporary device permission were removed; current-780M testing is out of scope.
- D5 implementation and proof boundary are the realigned approved plan in doc-8, established by the same asked-and-answered exchanges.
- D6 the familiar non-conflicting default PTT key is F9, selected by the operator during 2026-08-02 acceptance. After physical PTT succeeded, the operator settled CPU adoption as no-go: “Yeah, it worked. It was also enormously slow. If this is the performance we expect out of it, this is a no-go.”
- D7 no-go delivery preserves the inert reviewed sidecar/tests and research report for a dedicated-GPU revisit while removing the complete 2.6 GB local evaluation runtime, settled by the operator's 2026-08-02 selection of “Keep prototype, remove runtime.”
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 CrisperWhisper 2.0 turbo intended mode runs through CT2 CPU/INT8 on synthetic/non-private 5–60 s clips; cold/warm latency, real-time factor, output, and CPU usability are recorded. The failed 780M ROCm attempt and completed rollback are recorded without retaining GPU runtime state.
- [x] #2 A minimal global X11 hold-to-talk sidecar captures the focused Herdr pane at key-down, records while held, transcribes on release with one warm CT2 CPU model, and delivers through `herdr pane send-text`; concurrent presses and target/recording/inference/delivery failures fail closed, with Handy unchanged.
- [x] #3 Synthetic correction examples receive a documented intended-mode faithfulness read, including preservation of contradictions/ambiguous corrections; setup, cache footprint, rollback, license limits, measured findings, and recommendation are recorded in the attached research report.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Realigned approved implementation and proof plan: doc-8 (CPU-only; current-780M testing rolled back and out of scope).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented the two-file PTT prototype with 22 passing unit tests and zero LSP diagnostics. Measured CT2 CPU/INT8 on synthetic 5/15/30/60 s clips; warm waits were 7.49/8.43/9.11/22.40 s. Verified a real X11 Pause grab, bounded SIGTERM cleanup, and synthetic recorder→intended-mode→real Herdr delivery. The 780M ROCm trial was stopped after native HIP aborts destabilized the desktop; all GPU state and temporary device access were rolled back per operator direction.

Operator UAT: physical F9 hold-to-talk reached the original Herdr pane and failed closed on a near-empty tap. Operator disposition: the working CPU path is enormously slow and is a no-go. Updated the default trial key from the unfamiliar Pause/Break key to operator-selected F9; CPU prototype remains evidence for a future dedicated-GPU revisit, not an adopted service.

No-go delivery settled: preserve the inert reviewed sidecar/tests and research report for a dedicated-GPU revisit; remove the full 2.6 GB local evaluation runtime. Cleanup verified the runtime root is absent and no sidecar process remains.

Final diagnostic hardening: runtime-directory access probe failures now report cleanly; recorder startup cleanup uses specific exception classes while still covering interrupt/exit/generator paths; malformed numeric metadata cannot escape parsing. Focused suite is now 25 passing tests; primary LSP has zero diagnostics; active pi-lens scan reports no issues.

Final verification: 25 focused tests pass; py_compile passes; help reports F9; primary Python LSP has zero diagnostics; active pi-lens full scan reports no issues. Four independent fresh-context review passes found no material issues. Physical UAT proved exact-pane PTT delivery and settled CPU adoption as no-go. Runtime root is absent and no sidecar process remains.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Built a contained, fail-closed X11 F9 PTT research prototype that captures the Herdr pane at key-down and delivers CrisperWhisper intended-mode text. CT2 CPU/INT8 worked on synthetic 5–60 s clips, but measured and physical acceptance showed an enormous release-to-text delay, so the operator settled CPU adoption as no-go. The 780M ROCm attempt was fully rolled back; the inert reviewed prototype/report remain for a dedicated-GPU revisit, and the complete 2.6 GB local runtime was removed. Verified by 25 focused tests, py_compile, zero primary-LSP findings, a clean active pi-lens scan, real Herdr delivery, operator UAT, and four PASS/no-findings reviews.
<!-- SECTION:FINAL_SUMMARY:END -->
