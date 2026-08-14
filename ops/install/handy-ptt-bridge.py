#!/usr/bin/env python3
"""Left-Control q mode bridge for Handy on X11.

The bridge replaces the old hold-to-talk workflow with explicit q mode:

- Left-Control arms q mode (dynamically grabbing Space and Delete) or,
  while armed, cancels any active operation and exits q mode.
- While armed, each distinct physical Space press toggles recording
  (start/submit); releases and X11 auto-repeat pairs do nothing.
- While armed, each distinct physical Delete press cancels the active
  recording or in-flight transcription without delivery, staying armed.

The bridge communicates with Handy through explicit realtime signals:
SIGRTMIN+2 prepare, SIGRTMIN+3 mode-on, SIGRTMIN+4 mode-off,
SIGRTMIN+5 Space, and SIGRTMIN+6 Delete. The legacy PTT pair stays on SIGRTMIN and
SIGRTMIN+1. Handy restarts default to q mode off: if the target Handy PID
disappears or changes while the bridge is armed, the bridge resets q mode to
off, releases the Space/Delete grabs, and drops the pending action; a later
explicit arm emits mode-on to the current Handy process.
"""

import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

from Xlib import X, XK, display
from Xlib.error import XError


HANDY = str(Path.home() / ".local" / "bin" / "handy")
MODE_KEY = "Control_L"
START_STOP_KEY = "space"
CANCEL_KEY = "Delete"

# Distinct from the legacy PTT pair (SIGRTMIN / SIGRTMIN+1), which the
# app still serves for the preserved push-to-talk behavior.
MODE_PREPARE_SIGNAL = signal.SIGRTMIN + 2
MODE_ON_SIGNAL = signal.SIGRTMIN + 3
MODE_OFF_SIGNAL = signal.SIGRTMIN + 4
SPACE_SIGNAL = signal.SIGRTMIN + 5
DELETE_SIGNAL = signal.SIGRTMIN + 6

# Plain Space/Delete are mode controls even when Caps Lock or Num Lock is on.
# Modified chords (Ctrl/Alt/Super+Space) keep their existing desktop/app behavior.
GRAB_MODIFIERS = (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask)
PID_POLL_SECONDS = 0.25
READY_WAIT_ATTEMPTS = 100
READY_WAIT_SECONDS = 0.1
MODE_ACK_ATTEMPTS = 40
MODE_ACK_SECONDS = 0.05
ARM_GRAB_ATTEMPTS = 5
ARM_GRAB_RETRY_SECONDS = 0.05
READY_FILE = "qq-dictation-handy-ready"


def log(message):
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def handy_pid():
    result = subprocess.run(
        ["pgrep", "-n", "-x", "handy"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def ready_path():
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    return Path(runtime_dir) / READY_FILE if runtime_dir else None


def handy_state(pid):
    path = ready_path()
    if path is None:
        return None
    try:
        fields = path.read_text(encoding="utf-8").split()
        if len(fields) != 2 or int(fields[0]) != pid:
            return None
        return fields[1] if fields[1] in {"ready", "prepared", "armed"} else None
    except (OSError, ValueError):
        return None


def handy_ready(pid):
    return handy_state(pid) is not None


def wait_until_state(pid, expected, attempts, delay):
    for _ in range(attempts):
        if handy_pid() != pid:
            return None
        if handy_state(pid) == expected:
            return pid
        time.sleep(delay)
    return None


def wait_until_ready(pid):
    for _ in range(READY_WAIT_ATTEMPTS):
        if handy_pid() != pid:
            return None
        if handy_ready(pid):
            return pid
        time.sleep(READY_WAIT_SECONDS)
    return None


def ensure_handy():
    pid = handy_pid()
    if pid is None:
        log("Handy is not running; starting it hidden")
        subprocess.Popen(
            [HANDY, "--start-hidden"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        for _ in range(READY_WAIT_ATTEMPTS):
            time.sleep(READY_WAIT_SECONDS)
            pid = handy_pid()
            if pid is not None:
                break
    if pid is None:
        return None
    return wait_until_ready(pid)


class DistinctPressTracker:
    """Classifies key events into distinct physical presses.

    X11 key auto-repeat does not repeat the original press: while a key is
    held, the server emits a stream of synthesized release/press pairs that
    share one timestamp, followed by the single genuine release on key-up.
    A press is therefore a real, distinct press only when the key was not
    already reported held AND its timestamp differs from the immediately
    preceding (synthesized) release. Releases never count as presses.
    """

    def __init__(self):
        self._held = False
        self._last_release_time = None

    def on_press(self, timestamp):
        """Return True for a distinct physical press; False for repeats."""
        if self._held:
            return False
        if self._last_release_time == timestamp:
            # Synthesized auto-repeat press paired with its synthetic release.
            return False
        self._held = True
        return True

    def on_release(self, timestamp):
        self._held = False
        self._last_release_time = timestamp

    def reset(self):
        """Forget held/repeat state, e.g. across an arm/exit transition.

        Space/Delete events are only visible while grabbed; a key released
        while q mode was off leaves no release event behind, so a stale
        held flag must not swallow the next distinct press.
        """
        self._held = False
        self._last_release_time = None


def grab_keys(dpy, root, keycodes, report_failure=True):
    """Atomically acquire passive grabs or release the partial set.

    X protocol errors are asynchronous. Supplying an ``onerror`` callback and
    synchronising is therefore required; ``try/except BadAccess`` alone can
    print an error while incorrectly reporting success.
    """
    grabbed = []
    protocol_errors = []

    def record_error(exc, _request):
        protocol_errors.append(exc)

    try:
        for keycode in keycodes:
            for modifiers in GRAB_MODIFIERS:
                root.grab_key(
                    keycode,
                    modifiers,
                    True,
                    X.GrabModeAsync,
                    X.GrabModeAsync,
                    onerror=record_error,
                )
                grabbed.append((keycode, modifiers))
        dpy.sync()
    except XError as exc:
        protocol_errors.append(exc)

    if not protocol_errors:
        return True

    if report_failure:
        log("ERROR: a q mode key is already grabbed by another application")
    for keycode, modifiers in grabbed:
        root.ungrab_key(keycode, modifiers, onerror=lambda *_: None)
    try:
        dpy.sync()
    except XError:
        pass
    return False


def ungrab_keys(dpy, root, keycodes):
    for keycode in keycodes:
        for modifiers in GRAB_MODIFIERS:
            root.ungrab_key(keycode, modifiers, onerror=lambda *_: None)
    try:
        dpy.sync()
    except XError:
        pass


def next_event_or_timeout(dpy, timeout=PID_POLL_SECONDS):
    """Return the next X event, or ``None`` after a bounded PID-poll wait."""
    if dpy.pending_events():
        return dpy.next_event()
    ready, _, _ = select.select([dpy.fileno()], [], [], timeout)
    return dpy.next_event() if ready else None


def send_signal(pid, handy_signal, action):
    try:
        os.kill(pid, handy_signal)
        return True
    except OSError as exc:
        log(f"ERROR: Handy could not {action}: {exc}")
        return False


def main():
    dpy = display.Display()
    root = dpy.screen().root

    def keycode_for(keysym_name):
        keycode = dpy.keysym_to_keycode(XK.string_to_keysym(keysym_name))
        if keycode == 0:
            log(f"FATAL: {keysym_name} is not present in the active X11 keymap")
        return keycode

    mode_keycode = keycode_for(MODE_KEY)
    space_keycode = keycode_for(START_STOP_KEY)
    cancel_keycode = keycode_for(CANCEL_KEY)
    if 0 in (mode_keycode, space_keycode, cancel_keycode):
        return 1

    root.change_attributes(event_mask=X.KeyPressMask | X.KeyReleaseMask)
    if not grab_keys(dpy, root, (mode_keycode,)):
        log(f"FATAL: {MODE_KEY} is already grabbed by another application")
        return 1

    # A restarted bridge must not inherit app-side armed state from its
    # predecessor. If Handy is absent, its next process starts with q mode off.
    existing_pid = handy_pid()
    if existing_pid is not None and handy_ready(existing_pid):
        send_signal(existing_pid, MODE_OFF_SIGNAL, "reset q mode")

    log(f"ready — press {MODE_KEY} to arm or exit q mode")

    mode_on = False
    control = DistinctPressTracker()
    space = DistinctPressTracker()
    delete = DistinctPressTracker()
    signaled_pid = None

    def reset_local_mode(reason=None):
        nonlocal mode_on, signaled_pid
        was_on = mode_on
        mode_on = False
        signaled_pid = None
        space.reset()
        delete.reset()
        if was_on:
            ungrab_keys(dpy, root, (space_keycode, cancel_keycode))
        if reason:
            log(reason)

    def current_handy_matches():
        pid = signaled_pid
        current_pid = handy_pid()
        if mode_on and current_pid == pid and handy_state(pid) == "armed":
            return True
        # The PID was previously validated under this signal protocol. If it is
        # still current (or pgrep transiently missed it), best-effort mode-off
        # before releasing grabs; a replacement PID starts off and is untouched.
        if mode_on and pid is not None and current_pid in (None, pid):
            send_signal(pid, MODE_OFF_SIGNAL, "recover from lost mode acknowledgement")
        reset_local_mode(
            "Handy restarted or lost armed acknowledgement — q mode off; "
            "Space/Delete released"
        )
        return False

    def arm_mode():
        nonlocal mode_on, signaled_pid
        pid = ensure_handy()
        if pid is None:
            log("ERROR: Handy did not become ready; q mode remains off")
            return

        # Two-phase arm keeps the overlay truthful. Prepare acknowledges the
        # app/overlay without showing armed state; only after plain Space/Delete
        # grabs succeed does mode-on commit and show the legend. Every early exit
        # sends mode-off and releases partial grabs.
        rollback_required = True
        grabbed = False
        committed = False
        try:
            if not send_signal(pid, MODE_PREPARE_SIGNAL, "prepare q mode"):
                return
            if wait_until_state(
                pid,
                "prepared",
                MODE_ACK_ATTEMPTS,
                MODE_ACK_SECONDS,
            ) is None:
                log("ERROR: Handy did not prepare q mode")
                return

            for _ in range(ARM_GRAB_ATTEMPTS):
                if handy_pid() != pid:
                    return
                if grab_keys(
                    dpy,
                    root,
                    (space_keycode, cancel_keycode),
                    report_failure=False,
                ):
                    grabbed = True
                    break
                time.sleep(ARM_GRAB_RETRY_SECONDS)
            if not grabbed:
                log("ERROR: q mode not armed; Space/Delete left free")
                return

            if not send_signal(pid, MODE_ON_SIGNAL, "commit q mode"):
                return
            if wait_until_state(
                pid,
                "armed",
                MODE_ACK_ATTEMPTS,
                MODE_ACK_SECONDS,
            ) is None:
                log("ERROR: Handy did not commit q mode")
                return

            space.reset()
            delete.reset()
            signaled_pid = pid
            mode_on = True
            committed = True
            log(
                "q mode on — Space starts/stops, "
                "Delete cancels, Left-Control exits"
            )
        finally:
            if not committed:
                if grabbed:
                    ungrab_keys(dpy, root, (space_keycode, cancel_keycode))
                if rollback_required and handy_pid() in (None, pid):
                    send_signal(pid, MODE_OFF_SIGNAL, "roll back q mode")

    def disarm_mode():
        pid = signaled_pid
        # The PID was already validated when the mode committed. A transient
        # pgrep miss must not skip app-side disarm; a different PID is a new,
        # default-off process and is never signaled.
        if pid is not None and handy_pid() in (None, pid):
            send_signal(pid, MODE_OFF_SIGNAL, "exit q mode")
        reset_local_mode("q mode off")

    def deliver_action(handy_signal, action):
        if not current_handy_matches():
            return
        if not send_signal(signaled_pid, handy_signal, action):
            reset_local_mode(
                "Handy exited while armed — q mode off; "
                "Space/Delete released"
            )

    def stop_signal(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_signal)
    signal.signal(signal.SIGINT, stop_signal)

    try:
        while True:
            event = next_event_or_timeout(dpy)
            if event is None:
                if mode_on:
                    current_handy_matches()
                continue
            if event.type not in (X.KeyPress, X.KeyRelease):
                continue

            if event.detail == mode_keycode:
                if event.type == X.KeyRelease:
                    control.on_release(event.time)
                    continue
                if not control.on_press(event.time):
                    continue
                if mode_on:
                    disarm_mode()
                else:
                    arm_mode()
                continue

            if not mode_on:
                continue
            if event.detail == space_keycode:
                if event.type == X.KeyPress:
                    if space.on_press(event.time):
                        log("Space pressed")
                        deliver_action(SPACE_SIGNAL, "toggle recording")
                else:
                    space.on_release(event.time)
            elif event.detail == cancel_keycode:
                if event.type == X.KeyPress:
                    if delete.on_press(event.time):
                        log("Delete pressed")
                        deliver_action(DELETE_SIGNAL, "cancel dictation")
                else:
                    delete.on_release(event.time)
    except KeyboardInterrupt:
        pass
    finally:
        if mode_on:
            disarm_mode()
        ungrab_keys(dpy, root, (mode_keycode,))


if __name__ == "__main__":
    sys.exit(main())
