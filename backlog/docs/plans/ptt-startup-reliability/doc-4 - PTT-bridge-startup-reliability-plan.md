---
id: doc-4
title: PTT bridge startup reliability plan
type: specification
created_date: '2026-07-27 08:22'
updated_date: '2026-07-27 08:22'
---
# PTT bridge startup reliability plan

## Outcome

The right-Control PTT bridge starts reliably after a desktop restart on the operator's Cinnamon X11 workstation, including when the user systemd manager starts before the desktop environment is imported.

## Ownership boundary

Change only the packaged PTT user service and its installed copy. Keep Handy, transcription, key semantics, and desktop startup mechanisms unchanged.

## Non-goals

- Generalize the private package to arbitrary displays, Wayland sessions, or multi-seat systems.
- Change recording or transcription behavior.
- Replace systemd ownership with a Cinnamon autostart entry.

## Settled decision

Declare the workstation's stable `DISPLAY=:0` in the service. The operator approved this recommended smallest permanent fix in the 2026-07-27 alignment exchange after the runtime bridge was restored.

## Implementation

1. Use the boot journal as the failing reproducer: the service started before Cinnamon with no `DISPLAY`, exited with `Xlib.error.DisplayNameError`, retried five times, and hit the start limit.
2. Add the explicit X11 display to `packaging/handy-ptt.service`.
3. Deploy the same unit to `~/.config/systemd/user/handy-ptt.service`, reload systemd, and restart it.
4. Temporarily remove `DISPLAY` and `XAUTHORITY` from the user manager environment, restart the service, and verify it is active and logs its ready state; restore the manager environment.
5. Verify the packaged and installed units match, Python syntax remains valid, the service is active, and right-Control PTT still works.

## Success evidence

- Original boot journal establishes the pre-fix failure.
- A post-fix restart succeeds with the manager's display variables removed.
- The installed unit matches the packaged unit and remains active.
- Right-Control produces a successful operator dictation.
