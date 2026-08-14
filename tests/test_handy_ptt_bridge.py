"""Focused unit tests for the visible q mode X11 bridge."""

from __future__ import annotations

import importlib.util
import signal
import sys
import tempfile
import unittest
from collections.abc import Callable, Sequence
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from Xlib import X, XK


SCRIPT = Path(__file__).parents[1] / "ops" / "install" / "handy-ptt-bridge.py"
SPEC = importlib.util.spec_from_file_location("handy_ptt_bridge", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
bridge = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bridge
SPEC.loader.exec_module(bridge)


def event(kind, detail, timestamp):
    return SimpleNamespace(type=kind, detail=detail, time=timestamp)


def grabs_for(*keycodes):
    return [
        (keycode, modifiers)
        for keycode in keycodes
        for modifiers in bridge.GRAB_MODIFIERS
    ]


class DistinctPressTests(unittest.TestCase):
    def test_single_taps_are_distinct_and_repeat_holds_are_not(self):
        tracker = bridge.DistinctPressTracker()
        self.assertTrue(tracker.on_press(100))
        self.assertFalse(tracker.on_press(100))
        self.assertFalse(tracker.on_press(105))
        tracker.on_release(110)
        self.assertTrue(tracker.on_press(111))

    def test_x11_autorepeat_burst_emits_exactly_one_press(self):
        tracker = bridge.DistinctPressTracker()
        self.assertTrue(tracker.on_press(100))
        for repeated in range(6):
            tracker.on_release(101 + repeated)
            self.assertFalse(tracker.on_press(101 + repeated))
        tracker.on_release(110)
        self.assertTrue(tracker.on_press(111))

    def test_reset_clears_held_and_repeat_state(self):
        tracker = bridge.DistinctPressTracker()
        self.assertTrue(tracker.on_press(100))
        tracker.reset()
        self.assertTrue(tracker.on_press(200))
        tracker.on_release(210)
        tracker.reset()
        self.assertTrue(tracker.on_press(210))

    def test_release_never_emits_and_anchors_repeat_detection(self):
        tracker = bridge.DistinctPressTracker()
        tracker.on_release(100)
        self.assertFalse(tracker.on_press(100))
        self.assertTrue(tracker.on_press(101))


class ReadyTests(unittest.TestCase):
    def test_marker_must_name_the_current_process(self):
        with tempfile.TemporaryDirectory() as runtime_dir:
            marker = Path(runtime_dir) / bridge.READY_FILE
            with mock.patch.dict(
                bridge.os.environ, {"XDG_RUNTIME_DIR": runtime_dir}, clear=False
            ):
                self.assertFalse(bridge.handy_ready(42))
                marker.write_text("41 ready\n", encoding="utf-8")
                self.assertFalse(bridge.handy_ready(42))
                marker.write_text("42 unknown\n", encoding="utf-8")
                self.assertFalse(bridge.handy_ready(42))
                marker.write_text("42 prepared\n", encoding="utf-8")
                self.assertTrue(bridge.handy_ready(42))
                self.assertEqual(bridge.handy_state(42), "prepared")

    def test_existing_handy_is_not_returned_until_overlay_is_ready(self):
        with (
            mock.patch.object(bridge, "handy_pid", side_effect=[42, 42, 42]),
            mock.patch.object(bridge, "handy_ready", side_effect=[False, True]),
            mock.patch.object(bridge.time, "sleep"),
        ):
            self.assertEqual(bridge.ensure_handy(), 42)

    def test_pid_replacement_while_waiting_refuses_readiness(self):
        with (
            mock.patch.object(bridge, "handy_pid", return_value=99),
            mock.patch.object(bridge, "handy_ready") as ready,
        ):
            self.assertIsNone(bridge.wait_until_ready(42))
            ready.assert_not_called()


class SignalConstantTests(unittest.TestCase):
    def test_mode_signals_are_distinct_realtime_signals_within_bounds(self):
        signals = (
            bridge.MODE_PREPARE_SIGNAL,
            bridge.MODE_ON_SIGNAL,
            bridge.MODE_OFF_SIGNAL,
            bridge.SPACE_SIGNAL,
            bridge.DELETE_SIGNAL,
        )
        self.assertEqual(len(set(signals)), 5)
        self.assertEqual(bridge.MODE_PREPARE_SIGNAL, signal.SIGRTMIN + 2)
        self.assertEqual(bridge.MODE_ON_SIGNAL, signal.SIGRTMIN + 3)
        self.assertEqual(bridge.MODE_OFF_SIGNAL, signal.SIGRTMIN + 4)
        self.assertEqual(bridge.SPACE_SIGNAL, signal.SIGRTMIN + 5)
        self.assertEqual(bridge.DELETE_SIGNAL, signal.SIGRTMIN + 6)
        for number in signals:
            self.assertLessEqual(number, signal.SIGRTMAX)


class GrabTests(unittest.TestCase):
    def make_dpy_root(self, fail_at=None):
        root = SimpleNamespace(grab_calls=[], ungrab_calls=[])

        def grab_key(
            keycode,
            modifiers,
            owner,
            pointer_mode,
            keyboard_mode,
            onerror: Callable[[Exception, object | None], None] | None = None,
        ):
            del owner, pointer_mode, keyboard_mode
            root.grab_calls.append((keycode, modifiers))
            if fail_at is not None and len(root.grab_calls) == fail_at:
                assert onerror is not None
                onerror(RuntimeError("BadAccess"), None)

        def ungrab_key(keycode, modifiers, onerror=None):
            del onerror
            root.ungrab_calls.append((keycode, modifiers))

        root.grab_key = grab_key
        root.ungrab_key = ungrab_key
        return SimpleNamespace(sync=mock.Mock()), root

    def test_grab_keys_catches_plain_keys_across_lock_states(self):
        dpy, root = self.make_dpy_root()
        self.assertTrue(bridge.grab_keys(dpy, root, (9, 13)))
        self.assertEqual(root.grab_calls, grabs_for(9, 13))
        dpy.sync.assert_called_once_with()
        self.assertEqual(root.ungrab_calls, [])

    def test_async_grab_failure_releases_partial_set_and_refuses(self):
        dpy, root = self.make_dpy_root(fail_at=len(bridge.GRAB_MODIFIERS) + 1)
        self.assertFalse(bridge.grab_keys(dpy, root, (9, 13)))
        self.assertEqual(root.ungrab_calls, grabs_for(9, 13))
        self.assertEqual(dpy.sync.call_count, 2)

    def test_ungrab_keys_releases_every_lock_state(self):
        dpy, root = self.make_dpy_root()
        bridge.ungrab_keys(dpy, root, (9, 13))
        self.assertEqual(root.ungrab_calls, grabs_for(9, 13))
        dpy.sync.assert_called_once_with()


class BridgeLoopTests(unittest.TestCase):
    KEYCODES = {
        XK.string_to_keysym(bridge.MODE_KEY): 66,
        XK.string_to_keysym(bridge.START_STOP_KEY): 65,
        XK.string_to_keysym(bridge.CANCEL_KEY): 119,
    }

    def run_bridge(
        self,
        events,
        *,
        ensure_pids: Sequence[int] | None = None,
        handy_pids: int | None | Sequence[int | None] = 4242,
        fail_dynamic_grab: bool = False,
        state_wait_effect=None,
        handy_states: str | None | Sequence[str | None] = "armed",
        kill_effect=None,
    ):
        root = SimpleNamespace(grab_calls=[], ungrab_calls=[])

        def grab_key(
            keycode,
            modifiers,
            owner,
            pointer_mode,
            keyboard_mode,
            onerror: Callable[[Exception, object | None], None] | None = None,
        ):
            del owner, pointer_mode, keyboard_mode
            root.grab_calls.append((keycode, modifiers))
            mode_code = self.KEYCODES[XK.string_to_keysym(bridge.MODE_KEY)]
            if fail_dynamic_grab and keycode != mode_code:
                assert onerror is not None
                onerror(RuntimeError("BadAccess"), None)

        def ungrab_key(keycode, modifiers, onerror=None):
            del onerror
            root.ungrab_calls.append((keycode, modifiers))

        root.grab_key = grab_key
        root.ungrab_key = ungrab_key
        root.change_attributes = mock.Mock()
        screen = SimpleNamespace(root=root)
        dpy = SimpleNamespace(
            keysym_to_keycode=lambda keysym: self.KEYCODES[keysym],
            screen=lambda: screen,
            sync=mock.Mock(),
        )
        if ensure_pids is None:
            ensure_patch = mock.patch.object(bridge, "ensure_handy", return_value=4242)
        else:
            ensure_patch = mock.patch.object(
                bridge, "ensure_handy", side_effect=iter(ensure_pids)
            )
        if isinstance(handy_pids, (list, tuple)):
            handy_patch = mock.patch.object(
                bridge, "handy_pid", side_effect=iter(handy_pids)
            )
        else:
            handy_patch = mock.patch.object(
                bridge, "handy_pid", return_value=handy_pids
            )
        if isinstance(handy_states, (list, tuple)):
            state_patch = mock.patch.object(
                bridge, "handy_state", side_effect=iter(handy_states)
            )
        else:
            state_patch = mock.patch.object(
                bridge, "handy_state", return_value=handy_states
            )
        kill_patch = mock.patch.object(bridge.os, "kill", side_effect=kill_effect)
        with (
            mock.patch.object(bridge.display, "Display", return_value=dpy),
            mock.patch.object(
                bridge,
                "next_event_or_timeout",
                side_effect=list(events) + [KeyboardInterrupt()],
            ),
            ensure_patch,
            handy_patch,
            mock.patch.object(bridge, "handy_ready", return_value=True),
            state_patch,
            mock.patch.object(
                bridge,
                "wait_until_state",
                side_effect=state_wait_effect
                or (lambda pid, _state, _attempts, _delay: pid),
            ),
            mock.patch.object(bridge.time, "sleep"),
            kill_patch as kill,
            mock.patch.object(bridge.signal, "signal"),
        ):
            self.assertEqual(bridge.main(), None)
        return root, kill

    def test_startup_explicitly_resets_existing_handy(self):
        root, kill = self.run_bridge([])
        self.assertEqual(
            [call.args for call in kill.call_args_list],
            [(4242, bridge.MODE_OFF_SIGNAL)],
        )
        self.assertEqual(root.grab_calls, grabs_for(66))

    def test_arm_distinct_actions_and_exit(self):
        mode_code = self.KEYCODES[XK.string_to_keysym(bridge.MODE_KEY)]
        space_code = self.KEYCODES[XK.string_to_keysym(bridge.START_STOP_KEY)]
        delete_code = self.KEYCODES[XK.string_to_keysym(bridge.CANCEL_KEY)]
        events = [
            event(X.KeyPress, mode_code, 100),
            event(X.KeyRelease, mode_code, 101),
            event(X.KeyPress, space_code, 102),
            event(X.KeyRelease, space_code, 103),
            event(X.KeyPress, space_code, 103),  # X11 repeat pair
            event(X.KeyRelease, space_code, 104),
            event(X.KeyPress, delete_code, 105),
            event(X.KeyRelease, delete_code, 106),
            event(X.KeyPress, mode_code, 107),
            event(X.KeyRelease, mode_code, 108),
        ]
        root, kill = self.run_bridge(events)
        self.assertEqual(
            [call.args[1] for call in kill.call_args_list],
            [
                bridge.MODE_OFF_SIGNAL,  # startup reset
                bridge.MODE_PREPARE_SIGNAL,
                bridge.MODE_ON_SIGNAL,
                bridge.SPACE_SIGNAL,
                bridge.DELETE_SIGNAL,
                bridge.MODE_OFF_SIGNAL,
            ],
        )
        self.assertEqual(
            root.grab_calls,
            grabs_for(mode_code, space_code, delete_code),
        )
        self.assertIn((space_code, 0), root.ungrab_calls)
        self.assertIn((delete_code, 0), root.ungrab_calls)
        self.assertIn((mode_code, 0), root.ungrab_calls)

    def test_failed_dynamic_grab_refuses_mode_on(self):
        mode_code = self.KEYCODES[XK.string_to_keysym(bridge.MODE_KEY)]
        root, kill = self.run_bridge(
            [event(X.KeyPress, mode_code, 100)],
            handy_pids=[None, *([4242] * 7)],
            fail_dynamic_grab=True,
        )
        self.assertEqual(
            [call.args[1] for call in kill.call_args_list],
            [bridge.MODE_PREPARE_SIGNAL, bridge.MODE_OFF_SIGNAL],
        )
        self.assertEqual(
            root.grab_calls[: len(bridge.GRAB_MODIFIERS)], grabs_for(mode_code)
        )

    def test_shutdown_while_prepared_rolls_back_app_state(self):
        mode_code = self.KEYCODES[XK.string_to_keysym(bridge.MODE_KEY)]

        def interrupt(_pid, _state, _attempts, _delay):
            raise KeyboardInterrupt

        root, kill = self.run_bridge(
            [event(X.KeyPress, mode_code, 100)],
            ensure_pids=[100],
            handy_pids=[None, 100],
            state_wait_effect=interrupt,
        )
        self.assertEqual(
            [call.args for call in kill.call_args_list],
            [
                (100, bridge.MODE_PREPARE_SIGNAL),
                (100, bridge.MODE_OFF_SIGNAL),
            ],
        )
        self.assertEqual(root.grab_calls, grabs_for(mode_code))

    def test_shutdown_while_armed_disarms_on_transient_pid_miss(self):
        mode_code = self.KEYCODES[XK.string_to_keysym(bridge.MODE_KEY)]
        space_code = self.KEYCODES[XK.string_to_keysym(bridge.START_STOP_KEY)]
        root, kill = self.run_bridge(
            [
                event(X.KeyPress, mode_code, 100),
                event(X.KeyRelease, mode_code, 101),
            ],
            ensure_pids=[100],
            # Startup sees no app; arm sees pid 100; final disarm's pgrep misses.
            handy_pids=[None, 100, None],
        )
        self.assertEqual(
            [call.args for call in kill.call_args_list],
            [
                (100, bridge.MODE_PREPARE_SIGNAL),
                (100, bridge.MODE_ON_SIGNAL),
                (100, bridge.MODE_OFF_SIGNAL),
            ],
        )
        self.assertIn((space_code, 0), root.ungrab_calls)

    def test_interrupt_at_prepare_delivery_still_rolls_back(self):
        mode_code = self.KEYCODES[XK.string_to_keysym(bridge.MODE_KEY)]
        delivered_prepare = False

        def interrupt_once(_pid, handy_signal):
            nonlocal delivered_prepare
            if handy_signal == bridge.MODE_PREPARE_SIGNAL and not delivered_prepare:
                delivered_prepare = True
                raise KeyboardInterrupt

        root, kill = self.run_bridge(
            [event(X.KeyPress, mode_code, 100)],
            ensure_pids=[100],
            handy_pids=[None, 100],
            kill_effect=interrupt_once,
        )
        self.assertEqual(
            [call.args for call in kill.call_args_list],
            [
                (100, bridge.MODE_PREPARE_SIGNAL),
                (100, bridge.MODE_OFF_SIGNAL),
            ],
        )
        self.assertEqual(root.grab_calls, grabs_for(mode_code))

    def test_lost_armed_marker_disarms_before_releasing_keys(self):
        mode_code = self.KEYCODES[XK.string_to_keysym(bridge.MODE_KEY)]
        space_code = self.KEYCODES[XK.string_to_keysym(bridge.START_STOP_KEY)]
        root, kill = self.run_bridge(
            [
                event(X.KeyPress, mode_code, 100),
                event(X.KeyRelease, mode_code, 101),
                None,
            ],
            ensure_pids=[100],
            handy_pids=[None, 100, 100],
            handy_states=[None],
        )
        self.assertEqual(
            [call.args for call in kill.call_args_list],
            [
                (100, bridge.MODE_PREPARE_SIGNAL),
                (100, bridge.MODE_ON_SIGNAL),
                (100, bridge.MODE_OFF_SIGNAL),
            ],
        )
        self.assertIn((space_code, 0), root.ungrab_calls)

    def test_disarmed_space_and_delete_are_ignored(self):
        space_code = self.KEYCODES[XK.string_to_keysym(bridge.START_STOP_KEY)]
        delete_code = self.KEYCODES[XK.string_to_keysym(bridge.CANCEL_KEY)]
        _root, kill = self.run_bridge(
            [
                event(X.KeyPress, space_code, 100),
                event(X.KeyRelease, space_code, 101),
                event(X.KeyPress, delete_code, 102),
                event(X.KeyRelease, delete_code, 103),
            ],
            handy_pids=None,
        )
        kill.assert_not_called()

    def test_replaced_handy_drops_action_and_releases_dynamic_keys(self):
        mode_code = self.KEYCODES[XK.string_to_keysym(bridge.MODE_KEY)]
        space_code = self.KEYCODES[XK.string_to_keysym(bridge.START_STOP_KEY)]
        events = [
            event(X.KeyPress, mode_code, 100),  # arm pid 100
            event(X.KeyRelease, mode_code, 101),
            event(X.KeyPress, space_code, 102),  # detects pid 200
            event(X.KeyRelease, space_code, 103),
            event(X.KeyPress, mode_code, 104),  # explicit re-arm pid 200
            event(X.KeyRelease, mode_code, 105),
        ]
        root, kill = self.run_bridge(
            events,
            ensure_pids=[100, 200],
            # startup absent; first arm sees 100; action sees replacement;
            # second arm/final cleanup see pid 200.
            handy_pids=[None, 100, 200, 200, 200],
        )
        self.assertEqual(
            [call.args for call in kill.call_args_list],
            [
                (100, bridge.MODE_PREPARE_SIGNAL),
                (100, bridge.MODE_ON_SIGNAL),
                (200, bridge.MODE_PREPARE_SIGNAL),
                (200, bridge.MODE_ON_SIGNAL),
                (200, bridge.MODE_OFF_SIGNAL),
            ],
        )
        self.assertNotIn(bridge.SPACE_SIGNAL, [c.args[1] for c in kill.call_args_list])
        self.assertIn((space_code, 0), root.ungrab_calls)
        self.assertEqual(root.grab_calls.count((space_code, 0)), 2)

    def test_pid_poll_resets_mode_before_another_key_event(self):
        mode_code = self.KEYCODES[XK.string_to_keysym(bridge.MODE_KEY)]
        space_code = self.KEYCODES[XK.string_to_keysym(bridge.START_STOP_KEY)]
        root, kill = self.run_bridge(
            [
                event(X.KeyPress, mode_code, 100),
                event(X.KeyRelease, mode_code, 101),
                None,  # poll timeout observes disappeared Handy
                event(X.KeyPress, space_code, 102),
            ],
            ensure_pids=[100],
            handy_pids=[None, 100, None],
        )
        self.assertEqual(
            [call.args for call in kill.call_args_list],
            [
                (100, bridge.MODE_PREPARE_SIGNAL),
                (100, bridge.MODE_ON_SIGNAL),
                (100, bridge.MODE_OFF_SIGNAL),
            ],
        )
        self.assertIn((space_code, 0), root.ungrab_calls)


if __name__ == "__main__":
    unittest.main()
