---
id: doc-6
title: TASK-7 second-pass investigation plan
type: other
created_date: '2026-08-02 04:45'
updated_date: '2026-08-02 04:45'
---
# TASK-7 plan: LLM second-pass investigation

Approved by the operator in the 2026-08-01 session ("that all tracks"), with the
Whisper-speed addendum from the same session ("are we running the current
dictation model as fast as we can?").

## Boundary (operator direction, 2026-08-01)

- D1 research-only: no implementing, enabling, installing, downloading, or purchasing.
- D2 do not resurrect the retired FDT system; mine its history only as evidence.
- D3 no uploading recordings or transcript history; no inspecting private
  transcript history without explicit operator agreement; synthetic examples and
  public data are the default.
- D4 do not entangle with the open native-overlay Change (PR 9).

## Steps

1. Orient: repository, TASK-5/FDT history, built-in Handy post-processing hook,
   deterministic text filter, machine hardware (read-only).
2. Define second-pass scope: safe deterministic cleanup vs semantic rewriting;
   fail-safe-on-ambiguity; never answer/execute dictated content.
3. Delegate two read-only researcher tickets (research skill + delegate-batch):
   (a) local small-LLM feasibility on this machine + RX 6400 scenario;
   (b) Whisper acceleration levers (operator addendum). Owner verifies
   load-bearing citations.
4. Owner-run hosted-provider sweep from primary sources: current pricing,
   speed, streaming, retention/privacy, integration fit with Handy's hook.
5. Owner-measured current-state latency from Handy timing telemetry only
   (no transcript content).
6. Synthesize: candidate comparison, local co-residency assessment, evaluation
   plan (synthetic corpus, error taxonomy, semantic-preservation checks,
   p50/p95 budgets, quality thresholds, baselines incl. no-second-pass and
   deterministic-only), and one preferred architecture + alternatives with
   staged experiment and stop/rollback criteria.
7. Deliver: one cited, confidence-tagged Backlog research document attached to
   TASK-7, riding chore branch `chore/task-7-second-pass-research` through one
   pull request; concise recommendation reported in the accountable session.

## Assumptions recorded with the operator

- Hosted processing of dictated text is acceptable in principle (operator
  expects hosted may be more practical); provider retention terms are compared
  explicitly so the final choice is informed.
- "Relatively low cost" is quantified per-dictation and per-month across usage
  bands rather than assumed as a ceiling.
- Local feasibility rests on hardware inventory plus published measurements,
  labeled as estimates; any hands-on model download or benchmark is a separate,
  later approval.
