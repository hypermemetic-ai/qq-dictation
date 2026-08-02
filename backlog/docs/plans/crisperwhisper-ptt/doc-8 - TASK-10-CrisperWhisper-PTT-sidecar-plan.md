---
id: doc-8
title: TASK-10 CrisperWhisper PTT sidecar plan
type: other
created_date: '2026-08-02 16:03'
updated_date: '2026-08-02 16:51'
---
# TASK-10 plan: CrisperWhisper 2.0 system-wide PTT sidecar

Approved through the operator's 2026-08-02 asked-and-answered exchanges:

- Integration route: “I want push to talk… let's just pursue that route.” The Change will build a system-wide X11 hold-to-talk sidecar rather than depend on Handy or a third-party Pi extension.
- Backend selection initially authorized a 780M-versus-CPU benchmark. After repeated ROCm inference aborted with native HIP launch failures and destabilized the desktop, the operator realigned on 2026-08-02: “roll back everything you've done with the GPU… We'll stick to CPU for now. We'll reconsider GPU when the dedicated one comes in.” The GPU environment, results, cache, crash dumps, and temporary device ACL were removed. This Change now evaluates CT2 CPU/INT8 only.
- Comparison boundary: the operator will run the Whisper/Cerebras A/B separately; this Change does not depend on or report that comparison.

## Outcome and proof

Deliver a contained, reversible CrisperWhisper 2.0 turbo intended-mode trial and a minimal global push-to-talk path into Herdr, without changing Handy.

Done means fresh Checks demonstrate all of the following:

1. CrisperWhisper 2.0 turbo transcribes synthetic or explicitly non-private audio in `mode="intended"` through CT2 CPU/INT8.
2. Identical 5-, 15-, 30-, and 60-second clips produce cold/warm latency and real-time-factor evidence sufficient to judge whether CPU execution is usable. The report records that the 780M path was attempted, proved operationally unsafe on this host, and was fully rolled back without preserving GPU runtime state.
3. A global X11 hold-to-talk key captures the focused Herdr pane at key-down, records while held, transcribes on release with one warm model, and delivers through `herdr pane send-text`. Missing/changed targets, recorder failures, inference failures, and concurrent presses fail closed.
4. Synthetic correction examples receive a manual faithfulness read, with contradictions and ambiguous corrections preserved rather than silently resolved.
5. Setup, cache locations, removal, license limits, measured results, and remaining gaps are documented. Handy's running install, settings, PTT bridge, recordings, history, and logs remain untouched.

What cannot be proven without later operator judgment: whether intended-mode output is preferable for the operator's own speech and whether the chosen PTT key/feedback feels good in daily use. This Change proves the mechanism with synthetic/non-private evidence and leaves those experience judgments explicit.

## Boundary

- No Whisper+Cerebras comparison; no dependency on TASK-8 results.
- No access to `~/.local/share/com.pais.handy/`, operator recordings, transcript history, Handy logs, or secret files.
- No modification to Handy, its settings, or `packaging/handy-ptt-bridge.py` behavior.
- Standard turbo model only; no Pro model, purchase, or distribution.
- Treat use as a time-bounded evaluation. The model license clearly covers evaluation but may exclude ongoing operational deployment; document rather than reinterpret that gap.
- Keep venvs, pip cache, Hugging Face cache, converted model cache, benchmark audio, and logs under one removable TASK-10 runtime root outside the Repository.
- No further ROCm or current-780M work. The dedicated GPU is a later operator decision outside this Change.
- No privileged device-access change remains; the temporary `/dev/kfd` ACL was removed and prior access restored.

## Steps

1. **Reconcile Task and evidence.** Carry TASK-10 into this isolated Change, attach the approved plan and one confidence-tagged research report, and update the Task ledger and acceptance criteria to the operator's current boundary.
2. **Create the contained CPU runtime.** Use a supported Python version and explicit cache roots for CrisperWhisper CT2 CPU/INT8 plus conversion dependencies. Record exact versions and disk footprint. Preserve only a high-level note of the failed, rolled-back GPU attempt.
3. **Benchmark CPU.** Generate or use non-private synthetic clips at 5, 15, 30, and 60 seconds. Run cold and repeated warm transcriptions, recording wall time, package-reported processing time, real-time factor, output, failures, and peak memory where practical.
4. **Implement the smallest sidecar.** Add one focused Python entry point with seams for X11 PTT, Herdr pane capture, `ffmpeg` recording, warm CrisperWhisper inference, and delivery. Make backend/model/key/submit behavior explicit configuration. Reuse current reliability behavior conceptually—pane capture at recording start, newline collapse, bounded subprocesses, and fail-closed targeting—without importing or modifying Handy.
5. **Verify behavior.** Add focused tests for key state/autorepeat, single-flight behavior, target capture, argument-safe text delivery, newline handling, timeout/failure cleanup, and backend selection. Exercise end-to-end delivery with synthetic audio and a disposable Herdr target where available. Run LSP/diagnostics and the Repository's applicable test commands.
6. **Review and report.** Run fresh-context code review over the implementation and every fix delta. Update the research report with measured results, quality read, effort/rollback, and recommendation. Map each acceptance criterion to fresh evidence and deliver one pull request for operator merge.

## Stop conditions

- Do not resume ROCm or current-780M testing in this Change.
- If CT2 CPU is too slow for interactive PTT, preserve the measured prototype and recommend no adoption rather than adding speculative optimization.
- If the license blocks the intended next use, complete the evaluation evidence but do not operationalize or auto-start the sidecar.
