---
id: TASK-4
title: Make the PTT bridge survive desktop restart
status: Done
assignee: []
created_date: '2026-07-27 08:22'
updated_date: '2026-07-27 08:35'
labels: []
dependencies: []
documentation:
  - doc-4
modified_files:
  - packaging/handy-ptt.service
priority: high
type: bug
ordinal: 4000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
After a desktop restart, the enabled right-Control PTT bridge starts from the user manager before Cinnamon supplies its X11 environment. The bridge exits because DISPLAY is absent, exhausts systemd's start limit, and remains failed after the desktop is ready.

Outcome: make the packaged and installed PTT service start reliably at login on the operator's X11 workstation without changing Handy or transcription behavior.

## Decision ledger

- D1 X11 display source: declare the workstation's stable `DISPLAY=:0` in the service rather than add another autostart mechanism or retry loop — disposition: operator approval of the recommended smallest permanent fix in asked-and-answered alignment exchange 2026-07-27.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The bridge starts at login even when the user manager has no inherited DISPLAY
- [x] #2 Right-Control still starts and stops Handy recording
- [x] #3 The installed unit contains the durable fix and is active after a clean service restart
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Preserve the boot-journal reproducer showing the service starts without DISPLAY and exhausts its restart limit.
2. Give the packaged user service the workstation's stable X11 display explicitly.
3. Deploy that unit locally and restart the bridge.
4. Remove DISPLAY and XAUTHORITY from the user manager environment, restart the service, and verify it remains active and connects to X11; restore the manager environment afterward.
5. Run syntax/unit verification, fresh-context review, and present the diff and runtime evidence.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Boot evidence established the causal race: `handy-ptt.service` started at 03:01:05 with no `DISPLAY`, Cinnamon initialized around 03:01:12, and five Xlib `Bad display name ""` failures exhausted systemd's start limit. Cinnamon leaves `graphical-session.target` inactive, so the existing ordering line was not a readiness gate.

Implementation adds only `Environment=DISPLAY=:0` to the packaged user unit (production LOC +1/-0; decision points +0/-0). The installed copy was replaced from the packaged file and systemd reloaded.

Runtime Check removed both `DISPLAY` and `XAUTHORITY` from the user manager, restarted the service, and observed an active bridge whose process environment contained `DISPLAY=:0` and whose fresh journal emitted the ready message. The manager environment was then restored; packaged and installed units compare byte-for-byte. `systemd-analyze --user verify`, `git diff --check`, and Python bridge syntax passed.

The service maintenance restart overlapped an operator key hold, which left Handy recording after the bridge process exited. Handy was restarted immediately to discard that interrupted recording without transcribing or delivering it; the installed project executable and bridge both returned active.

The mandated implementer and reviewer engines failed closed before execution because qq-dispatch classified this Repository worktree as unrelated; no child mutation occurred. Independent read-only intercom review found one generated `packaging/__pycache__/*.pyc` created by the owner's syntax Check. It was removed. A separate independent fix-delta review passed with no material finding and confirmed only the expected service, Task, and plan paths remain.

Post-deployment UAT: the operator completed multiple right-Control press/release dictations after the permanent service deployment. The bridge journal recorded matching start/stop pairs, the messages arrived successfully, and the operator explicitly accepted the PTT behavior on 2026-07-27.

GitHub PR #5 reports no repository checks for this branch; all applicable local and installed-runtime Checks are green.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Fixed the reboot failure by declaring the operator workstation's stable `DISPLAY=:0` in the packaged right-Control PTT user service. The installed unit now starts and connects to X11 even when the user systemd manager has no inherited desktop variables; Handy, recording, key, and transcription behavior are unchanged.

Evidence: the boot journal reproduced the missing-DISPLAY/start-limit failure; a post-fix restart with manager `DISPLAY` and `XAUTHORITY` removed stayed active and logged ready; packaged and installed units match; systemd verification and diff checks pass; independent fix-delta review passed; and the operator accepted multiple successful post-deployment right-Control dictations. PR #5 has no configured GitHub checks.
<!-- SECTION:FINAL_SUMMARY:END -->
