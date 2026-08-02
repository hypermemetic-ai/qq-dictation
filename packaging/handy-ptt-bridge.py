#!/usr/bin/env python3
"""Use the right Control key as hold-to-talk for Handy on X11."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from Xlib import X, XK, Xatom, Xutil, display, error


HANDY = str(Path.home() / ".local" / "bin" / "handy")
KEYSYM = "Control_R"
PTT_START_SIGNAL = signal.SIGRTMIN
PTT_STOP_SIGNAL = signal.SIGRTMIN + 1


class RecordingIndicator:
    """Small X11 recording badge that never becomes a managed taskbar window."""

    WIDTH = 220
    HEIGHT = 48
    BOTTOM_MARGIN = 76

    def __init__(self, dpy):
        self.dpy = dpy
        screen = dpy.screen()
        colormap = screen.default_colormap
        self.background = colormap.alloc_named_color("#202124").pixel
        self.foreground = colormap.alloc_named_color("#f1f3f4").pixel
        self.recording = colormap.alloc_named_color("#ef4444").pixel
        x = max(0, (screen.width_in_pixels - self.WIDTH) // 2)
        y = max(0, screen.height_in_pixels - self.HEIGHT - self.BOTTOM_MARGIN)
        self.window = screen.root.create_window(
            x,
            y,
            self.WIDTH,
            self.HEIGHT,
            0,
            X.CopyFromParent,
            X.InputOutput,
            X.CopyFromParent,
            background_pixel=self.background,
            override_redirect=True,
            save_under=True,
            event_mask=X.ExposureMask,
        )
        self.window.set_wm_name("qq-dictation recording")
        self.window.set_wm_hints(flags=Xutil.InputHint, input=0)
        window_type = dpy.intern_atom("_NET_WM_WINDOW_TYPE")
        notification_type = dpy.intern_atom("_NET_WM_WINDOW_TYPE_NOTIFICATION")
        self.window.change_property(window_type, Xatom.ATOM, 32, [notification_type])
        self.font = dpy.open_font("fixed")
        self.gc = self.window.create_gc(
            foreground=self.foreground,
            background=self.background,
            font=self.font,
        )

    def redraw(self):
        self.window.fill_rectangle(
            self.gc,
            0,
            0,
            self.WIDTH,
            self.HEIGHT,
        )
        self.gc.change(foreground=self.recording)
        self.window.fill_arc(self.gc, 18, 15, 18, 18, 0, 360 * 64)
        self.gc.change(foreground=self.foreground)
        self.window.draw_text(self.gc, 52, 30, "Recording - release to send")

    def show(self):
        self.window.map()
        self.window.configure(stack_mode=X.Above)
        self.redraw()
        self.dpy.sync()

    def hide(self):
        self.window.unmap()
        self.dpy.sync()


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


def ensure_handy():
    pid = handy_pid()
    if pid is not None:
        return pid

    log("Handy is not running; starting it hidden")
    subprocess.Popen(
        [HANDY, "--start-hidden"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(100):
        time.sleep(0.1)
        pid = handy_pid()
        if pid is not None:
            # The Unix signal handler is registered shortly after process
            # creation, so do not race it on a cold start.
            time.sleep(0.4)
            return pid
    return None


def signal_handy(handy_signal, action):
    pid = ensure_handy()
    if pid is None:
        log("ERROR: Handy did not become ready")
        return False
    try:
        os.kill(pid, handy_signal)
        return True
    except ProcessLookupError:
        log(f"ERROR: Handy exited before it could {action}")
        return False


def main():
    dpy = display.Display()
    root = dpy.screen().root
    indicator = RecordingIndicator(dpy)
    keycode = dpy.keysym_to_keycode(XK.string_to_keysym(KEYSYM))
    if keycode == 0:
        log(f"FATAL: {KEYSYM} is not present in the active X11 keymap")
        return 1

    lock_states = (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask)
    try:
        for modifiers in lock_states:
            root.grab_key(
                keycode,
                modifiers,
                True,
                X.GrabModeAsync,
                X.GrabModeAsync,
            )
        root.change_attributes(event_mask=X.KeyPressMask | X.KeyReleaseMask)
        dpy.sync()
    except error.BadAccess:
        log(f"FATAL: {KEYSYM} is already grabbed by another application")
        return 1

    log(f"ready — hold {KEYSYM} to record with Handy; release to transcribe")
    holding = False
    buffered = []

    def next_event():
        return buffered.pop(0) if buffered else dpy.next_event()

    while True:
        event = next_event()
        if event.type == X.Expose and event.window == indicator.window:
            indicator.redraw()
            continue
        if event.type not in (X.KeyPress, X.KeyRelease):
            continue
        if event.detail != keycode:
            continue

        if event.type == X.KeyPress:
            if not holding:
                holding = signal_handy(PTT_START_SIGNAL, "start recording")
                if holding:
                    indicator.show()
                    log("recording started")
            continue

        dpy.sync()
        if dpy.pending_events():
            following = next_event()
            if (
                following.type == X.KeyPress
                and following.detail == keycode
                and following.time == event.time
            ):
                continue
            buffered.insert(0, following)

        if holding:
            indicator.hide()
            if signal_handy(PTT_STOP_SIGNAL, "stop recording"):
                log("recording stopped; transcription requested")
            holding = False


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass
