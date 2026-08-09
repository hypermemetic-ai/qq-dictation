"""Hermetic tests for the production Linux/X11 remote laptop client."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "packaging" / "handy-remote-client.py"
SPEC = importlib.util.spec_from_file_location("handy_remote_client", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
client = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = client
SPEC.loader.exec_module(client)


def executable(path: Path, content: str) -> Path:
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    path.chmod(0o755)
    return path


def config(**updates):
    raw = {
        "ssh_host": "workstation-alias",
        "ghostty_title": "operator remote herdr",
        "ghostty_class": "com.mitchellh.ghostty",
        "capture_argv": list(client.DEFAULT_CAPTURE_ARGV),
        "ssh_path": "/usr/bin/ssh",
        "remote_helper": "~/.local/bin/handy-remote-stream.py",
        "notify_send_path": "/usr/bin/notify-send",
    }
    if updates.get("delivery_mode") == "local":
        raw.pop("ghostty_title")
        raw.pop("ghostty_class")
        raw["xdotool_path"] = "/usr/bin/xdotool"
    raw.update(updates)
    return client.ClientConfig.from_mapping(raw)


class ConfigAndProtocolTests(unittest.TestCase):
    def test_config_requires_exact_window_facts_and_safe_transport_values(self):
        self.assertEqual(config().capture_argv[-1], "-")
        for update, message in [
            ({"ssh_host": "host; touch /tmp/no"}, "safe SSH"),
            ({"remote_helper": "helper;bad"}, "unsafe shell"),
            ({"ghostty_title": "bad\ntitle"}, "control character"),
            ({"capture_argv": ["/usr/bin/pw-record", "-"]}, "--rate"),
            (
                {"capture_argv": ["/usr/bin/ffmpeg", "--rate", "16000", "--channels", "1", "--format", "s16", "-"]},
                "pw-record",
            ),
        ]:
            with self.subTest(update=update):
                with self.assertRaisesRegex(client.ClientError, message):
                    config(**update)

    def test_config_refuses_unknown_fields(self):
        with self.assertRaisesRegex(client.ClientError, "unknown fields"):
            client.ClientConfig.from_mapping(
                {
                    "ssh_host": "host",
                    "ghostty_title": "title",
                    "ghostty_class": "class",
                    "capture_argv": list(client.DEFAULT_CAPTURE_ARGV),
                    "password": "not allowed",
                }
            )

    def test_delivery_mode_schema_is_exact_and_mode_specific(self):
        self.assertEqual(config().delivery_mode, client.DeliveryMode.HERDR)
        local = config(delivery_mode="local")
        self.assertEqual(local.delivery_mode, client.DeliveryMode.LOCAL)
        self.assertIsNone(local.ghostty_title)
        self.assertEqual(local.xdotool_path, "/usr/bin/xdotool")
        with self.assertRaisesRegex(client.ClientError, "exactly"):
            config(delivery_mode="wayland")
        with self.assertRaisesRegex(client.ClientError, "must not name a Ghostty"):
            config(delivery_mode="local", ghostty_title="saved", ghostty_class="saved")
        with self.assertRaisesRegex(client.ClientError, "xdotool_path"):
            config(xdotool_path="/usr/bin/xdotool")

    def test_protocol_framing_version_and_request_matching_are_strict(self):
        frame = client.encode_message({"type": "status", "version": 1})
        self.assertEqual(int.from_bytes(frame[:4], "big"), len(frame) - 4)
        good = client.decode_response(
            b'{"version":1,"status":"ready","request_id":"r1"}'
        )
        self.assertEqual(
            client.validate_response(good, {"ready"}, "r1")["status"], "ready"
        )
        for payload, message in [
            (b'{"version":2,"status":"recording","request_id":"r"}', "version"),
            (b'{"version":1,"status":"recording","request_id":"r","extra":1}', "unknown"),
            (b'{"version":1,"status":"error","error":"refused"}', "refused"),
            (b"[]", "non-object"),
        ]:
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(client.ClientError, message):
                    client.decode_response(payload)
        with self.assertRaisesRegex(client.ClientError, "stale or different"):
            client.validate_response(good, {"ready"}, "r2")

    def test_injection_plan_is_bounded_strict_and_consuming_only(self):
        response = client.decode_response(
            b'{"version":1,"status":"succeeded","request_id":"r1",'
            b'"injection":{"text":"exact text ","submit_key":"ctrl_enter"}}'
        )
        validated = client.validate_response(
            response, {"succeeded"}, "r1", expect_injection=True
        )
        self.assertEqual(
            client.injection_plan(validated),
            client.InjectionPlan("exact text ", "ctrl_enter"),
        )
        with self.assertRaisesRegex(client.ClientError, "outside its consuming"):
            client.validate_response(response, {"succeeded"}, "r1")
        for injection, message in [
            ({"text": "x", "submit_key": "tab"}, "unsupported submit"),
            ({"text": "x"}, "malformed"),
            (
                {"text": "x" * (client.MAX_INJECTION_TEXT_BYTES + 1), "submit_key": None},
                "outside bounds",
            ),
        ]:
            payload = json.dumps(
                {
                    "version": 1,
                    "status": "succeeded",
                    "request_id": "r1",
                    "injection": injection,
                }
            ).encode()
            with self.subTest(message=message):
                with self.assertRaisesRegex(client.ClientError, message):
                    client.decode_response(payload)

    def test_status_notifier_uses_complete_finite_argv_for_every_state(self):
        notifier = client.StatusNotifier("/usr/bin/notify-send")
        expiries = {
            client.ClientState.OFF: "2000",
            client.ClientState.ARMED: "2000",
            client.ClientState.RECORDING: "2000",
            client.ClientState.PROCESSING: "2000",
            client.ClientState.FAILED: "8000",
        }
        self.assertEqual(set(expiries), set(client.ClientState))

        completed = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(client.subprocess, "run", return_value=completed) as run:
            for state in client.ClientState:
                notifier.show(state, f"{state.value} detail")

        self.assertEqual(run.call_count, len(client.ClientState))
        for call, state in zip(run.call_args_list, client.ClientState, strict=True):
            self.assertEqual(
                call.args[0],
                [
                    "/usr/bin/notify-send",
                    "--app-name=qq-dictation",
                    "--replace-id=25160",
                    f"--expire-time={expiries[state]}",
                    f"qq-dictation: {state.value}",
                    f"{state.value} detail",
                ],
            )
        self.assertEqual(
            notifier.history,
            [(state, f"{state.value} detail") for state in client.ClientState],
        )

    def test_xdotool_adapter_uses_argv_only_exact_text_and_one_submit_key(self):
        injector = client.XdotoolInjector(config(delivery_mode="local"))
        completed = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(client.subprocess, "run", return_value=completed) as run:
            injector.inject(client.InjectionPlan("exact -- Unicode Δ ", "cmd_enter"))
        self.assertEqual(run.call_count, 2)
        self.assertEqual(
            run.call_args_list[0].args[0],
            [
                "/usr/bin/xdotool",
                "type",
                "--delay",
                "0",
                "--clearmodifiers",
                "--",
                "exact -- Unicode Δ ",
            ],
        )
        self.assertEqual(
            run.call_args_list[1].args[0],
            [
                "/usr/bin/xdotool",
                "key",
                "--clearmodifiers",
                "super+Return",
            ],
        )
        self.assertTrue(all(call.kwargs["shell"] is False for call in run.call_args_list))

    def test_xdotool_text_failure_never_attempts_submit_key(self):
        injector = client.XdotoolInjector(config(delivery_mode="local"))
        failed = subprocess.CompletedProcess([], 1, "", "synthetic adapter failure")
        with mock.patch.object(client.subprocess, "run", return_value=failed) as run:
            with self.assertRaisesRegex(client.ClientError, "synthetic adapter failure"):
                injector.inject(client.InjectionPlan("one attempt", "enter"))
        self.assertEqual(run.call_count, 1)
        self.assertEqual(
            run.call_args.args[0],
            [
                "/usr/bin/xdotool",
                "type",
                "--delay",
                "0",
                "--clearmodifiers",
                "--",
                "one attempt",
            ],
        )

    def test_pcm_decoder_is_little_endian_bounded_and_refuses_partial_samples(self):
        self.assertEqual(client.pcm_s16le_samples(b"\x01\x00\x00\x80"), [1, -32768])
        with self.assertRaisesRegex(client.ClientError, "partial"):
            client.pcm_s16le_samples(b"\x01")
        with self.assertRaisesRegex(client.ClientError, "exceeds"):
            client.pcm_s16le_samples(b"\x00\x00" * (client.MAX_AUDIO_SAMPLES + 1))

    def test_protocol_pipe_eof_and_timeout_are_explicit_failures(self):
        read_fd, write_fd = os.pipe()
        os.close(write_fd)
        try:
            with self.assertRaisesRegex(client.ClientError, "truncated"):
                client.read_exact(read_fd, 1, 0.1)
        finally:
            os.close(read_fd)

        read_fd, write_fd = os.pipe()
        try:
            with self.assertRaisesRegex(client.ClientError, "timed out"):
                client.read_exact(read_fd, 1, 0.01)
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_distinct_press_tracker_refuses_autorepeat_pairs(self):
        tracker = client.DistinctPressTracker()
        self.assertTrue(tracker.on_press(100))
        tracker.on_release(101)
        self.assertFalse(tracker.on_press(101))
        tracker.on_release(102)
        self.assertTrue(tracker.on_press(103))
        tracker.reset()
        self.assertTrue(tracker.on_press(103))

    def test_ssh_and_capture_launches_are_argv_only_and_session_owned(self):
        configured = config()
        transport = client.ProtocolTransport(configured)
        process = mock.Mock(stdin=mock.Mock(), stdout=mock.Mock())
        with mock.patch.object(client.subprocess, "Popen", return_value=process) as popen:
            transport.start()
        self.assertEqual(
            popen.call_args.args[0],
            [
                "/usr/bin/ssh",
                "-T",
                "workstation-alias",
                "~/.local/bin/handy-remote-stream.py",
            ],
        )
        self.assertIs(popen.call_args.kwargs["shell"], False)
        self.assertIs(popen.call_args.kwargs["start_new_session"], True)


class FakeNotifier:
    def __init__(self):
        self.history = []

    def show(self, state, detail=""):
        self.history.append((state, detail))


class FakeX11:
    def __init__(self, fail_recheck=False, focus_failure_at=None):
        self.identity = client.WindowIdentity(
            1234, 5678, "operator remote herdr", "com.mitchellh.ghostty"
        )
        self.dynamic = False
        self.released = 0
        self.fail_recheck = fail_recheck
        self.rechecks = 0
        self.identity_reads = 0
        self.focus_checks = 0
        self.focus_failure_at = focus_failure_at

    def grab_dynamic(self):
        self.dynamic = True

    def release_dynamic(self):
        if self.dynamic:
            self.released += 1
        self.dynamic = False

    def active_identity(self):
        self.identity_reads += 1
        return self.identity

    def require_same_active(self, identity):
        self.rechecks += 1
        if self.fail_recheck:
            raise client.ClientError("active Ghostty window changed")
        if identity != self.identity:
            raise client.ClientError("wrong identity")

    def require_readable_focus(self):
        self.focus_checks += 1
        if self.focus_failure_at == self.focus_checks:
            raise client.ClientError("X11 did not report one focused window")


class FakeInjector:
    def __init__(self, fail=False):
        self.fail = fail
        self.plans = []

    def inject(self, plan):
        self.plans.append(plan)
        if self.fail:
            raise client.ClientError("synthetic adapter-reported failure")


class FakeTransport:
    def __init__(self, _config, terminal="succeeded"):
        self.delivery_mode = _config.delivery_mode
        self.started = False
        self.closed = False
        self.messages = []
        self.request_number = 0
        self.active_request = None
        self.phase = None
        self.processing_polls = 0
        self.terminal = terminal
        self.exited = False

    def start(self):
        self.started = True

    def exchange(self, message):
        self.messages.append(message)
        kind = message["type"]
        if kind == "start":
            if self.active_request is not None:
                raise client.ClientError("overlapping fake request")
            self.request_number += 1
            self.active_request = f"r{self.request_number}"
            self.phase = "recording"
            self.processing_polls = 0
            return {
                "version": 1,
                "status": "recording",
                "request_id": self.active_request,
            }

        request = message.get("request_id")
        if request != self.active_request:
            raise client.ClientError("stale fake request")
        if kind == "audio" and self.phase == "recording":
            return {"version": 1, "status": "accepted", "request_id": request}
        if kind == "finish" and self.phase == "recording":
            self.phase = "processing"
            return {"version": 1, "status": "processing", "request_id": request}
        if kind == "status" and self.phase == "processing":
            self.processing_polls += 1
            if self.processing_polls == 1:
                status = "processing"
            elif self.terminal == "blank":
                status = "succeeded"
                self.active_request = None
                self.phase = None
            elif self.terminal == "failed":
                status = "failed"
                self.active_request = None
                self.phase = None
            else:
                status = "ready"
                self.phase = "ready"
            return {"version": 1, "status": status, "request_id": request}
        if kind == "status" and self.phase == "ready":
            return {"version": 1, "status": "ready", "request_id": request}
        if kind == "commit" and self.phase == "ready":
            status = self.terminal
            self.active_request = None
            self.phase = None
            response = {"version": 1, "status": status, "request_id": request}
            if self.delivery_mode == client.DeliveryMode.LOCAL and status == "succeeded":
                response["injection"] = {
                    "text": "workstation text ",
                    "submit_key": "ctrl_enter",
                }
            return response
        if kind == "status" and self.phase == "cancelling":
            self.processing_polls += 1
            status = "cancelling" if self.processing_polls == 1 else "cancelled"
            if status == "cancelled":
                self.active_request = None
                self.phase = None
            return {"version": 1, "status": status, "request_id": request}
        if kind == "cancel":
            if self.phase == "processing":
                self.phase = "cancelling"
                self.processing_polls = 0
                return {"version": 1, "status": "cancelling", "request_id": request}
            self.active_request = None
            self.phase = None
            return {"version": 1, "status": "cancelled", "request_id": request}
        raise AssertionError((kind, self.phase))

    def unexpected_exit(self):
        return self.exited

    def close(self):
        self.closed = True


class FakeCapture:
    def __init__(self, _config, send_audio, fail):
        self.send_audio = send_audio
        self.fail_callback = fail
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True
        self.send_audio([1, -1, 2])

    def stop(self):
        self.stopped = True


class ApplicationStateTests(unittest.TestCase):
    def make_app(self, x11=None, terminal="succeeded", delivery_mode="herdr", injector=None):
        x11 = x11 or FakeX11()
        notifier = FakeNotifier()
        transports = []
        captures = []
        injector = injector or FakeInjector()
        configured = config(delivery_mode=delivery_mode) if delivery_mode == "local" else config()

        def transport_factory(value):
            transport = FakeTransport(value, terminal)
            transports.append(transport)
            return transport

        def capture_factory(value, send, fail):
            capture = FakeCapture(value, send, fail)
            captures.append(capture)
            return capture

        app = client.LaptopApplication(
            configured,
            x11,
            notifier,
            transport_factory,
            capture_factory,
            injector_factory=lambda _configured: injector,
        )
        return app, x11, notifier, transports, captures, injector

    def test_failed_dynamic_grab_never_starts_ssh_and_remains_nonrecording(self):
        class RefusingX11(FakeX11):
            def grab_dynamic(self):
                raise client.ClientError("Space or Delete is already grabbed")

        app, x11, _notifier, transports, _captures, _injector = self.make_app(RefusingX11())
        app.arm()
        self.assertEqual(app.state, client.ClientState.FAILED)
        self.assertFalse(x11.dynamic)
        self.assertEqual(transports, [])

    def test_start_audio_finish_ready_commit_and_terminal_states_are_ordered(self):
        app, x11, notifier, transports, captures, _injector = self.make_app()
        app.right_control()
        self.assertEqual(app.state, client.ClientState.ARMED)
        self.assertTrue(x11.dynamic)
        app.space()
        self.assertEqual(app.state, client.ClientState.RECORDING)
        self.assertEqual(app.start_window, x11.identity)
        kinds = [message["type"] for message in transports[0].messages]
        self.assertEqual(kinds[:2], ["start", "audio"])
        app.space()
        self.assertEqual(app.state, client.ClientState.PROCESSING)
        self.assertTrue(captures[0].stopped)
        app.tick()
        self.assertEqual(app.state, client.ClientState.PROCESSING)
        app.tick()
        self.assertEqual(app.state, client.ClientState.ARMED)
        self.assertEqual(
            [message["type"] for message in transports[0].messages][-2:],
            ["status", "commit"],
        )
        self.assertEqual(x11.rechecks, 1)
        self.assertEqual(
            [state for state, _ in notifier.history],
            [
                client.ClientState.OFF,
                client.ClientState.ARMED,
                client.ClientState.RECORDING,
                client.ClientState.PROCESSING,
                client.ClientState.ARMED,
            ],
        )

    def test_local_mode_has_no_start_target_and_injects_one_exact_workstation_plan(self):
        app, x11, notifier, transports, _captures, injector = self.make_app(
            delivery_mode="local"
        )
        app.arm()
        app.space()
        self.assertIsNone(app.start_window)
        self.assertEqual(x11.identity_reads, 0)
        self.assertEqual(transports[0].messages[0]["delivery_mode"], "local")
        app.space()
        app.tick()
        app.tick()

        self.assertEqual(app.state, client.ClientState.ARMED)
        self.assertEqual(x11.focus_checks, 2)
        self.assertEqual(
            injector.plans,
            [client.InjectionPlan("workstation text ", "ctrl_enter")],
        )
        self.assertEqual(
            [message["type"] for message in transports[0].messages].count("commit"), 1
        )
        self.assertIn("X11-focused window", notifier.history[-1][1])

    def test_local_success_releases_dynamic_grabs_around_one_injection_before_armed(self):
        app, x11, notifier, _transports, _captures, injector = self.make_app(
            delivery_mode="local"
        )
        ordered = mock.Mock()
        for name, owner in (
            ("grab_dynamic", x11),
            ("release_dynamic", x11),
            ("inject", injector),
            ("show", notifier),
        ):
            wrapped = mock.Mock(wraps=getattr(owner, name))
            setattr(owner, name, wrapped)
            ordered.attach_mock(wrapped, name)

        app.arm()
        app.space()
        app.space()
        app.tick()
        app.tick()

        plan = client.InjectionPlan("workstation text ", "ctrl_enter")
        self.assertEqual(
            ordered.mock_calls[-4:],
            [
                mock.call.release_dynamic(),
                mock.call.inject(plan),
                mock.call.grab_dynamic(),
                mock.call.show(
                    client.ClientState.ARMED,
                    "Injected into the X11-focused window at delivery",
                ),
            ],
        )
        self.assertEqual(app.state, client.ClientState.ARMED)

    def test_local_focus_refusal_before_or_after_handoff_never_injects(self):
        for failure_at, expected_commits in ((1, 0), (2, 1)):
            with self.subTest(failure_at=failure_at):
                app, x11, _notifier, transports, _captures, injector = self.make_app(
                    x11=FakeX11(focus_failure_at=failure_at), delivery_mode="local"
                )
                app.arm()
                app.space()
                app.space()
                app.tick()
                app.tick()
                self.assertEqual(app.state, client.ClientState.FAILED)
                self.assertFalse(x11.dynamic)
                self.assertEqual(injector.plans, [])
                self.assertEqual(
                    [message["type"] for message in transports[0].messages].count("commit"),
                    expected_commits,
                )

    def test_local_adapter_failure_is_one_marked_attempt_without_retry_or_fallback(self):
        failing = FakeInjector(fail=True)
        app, x11, notifier, transports, _captures, injector = self.make_app(
            delivery_mode="local", injector=failing
        )
        with mock.patch.object(
            x11, "grab_dynamic", wraps=x11.grab_dynamic
        ) as grab_dynamic:
            app.arm()
            app.space()
            app.space()
            app.tick()
            app.tick()

        self.assertEqual(app.state, client.ClientState.FAILED)
        self.assertTrue(app.injection_attempted is False)  # cleared only during terminal teardown
        self.assertEqual(
            injector.plans,
            [client.InjectionPlan("workstation text ", "ctrl_enter")],
        )
        self.assertEqual(grab_dynamic.call_count, 1)
        self.assertFalse(x11.dynamic)
        self.assertIn("adapter-reported failure", notifier.history[-1][1])
        self.assertEqual(
            [message["type"] for message in transports[0].messages].count("commit"), 1
        )
        app.tick()
        self.assertEqual(len(injector.plans), 1)

    def test_local_reacquisition_failure_after_injection_fails_without_repeat(self):
        app, x11, notifier, transports, _captures, injector = self.make_app(
            delivery_mode="local"
        )
        original_grab_dynamic = x11.grab_dynamic
        grab_attempts = 0

        def fail_reacquisition():
            nonlocal grab_attempts
            grab_attempts += 1
            if grab_attempts == 2:
                raise client.ClientError("Space or Delete reacquisition failed")
            original_grab_dynamic()

        x11.grab_dynamic = fail_reacquisition
        app.arm()
        app.space()
        app.space()
        app.tick()
        app.tick()

        self.assertEqual(app.state, client.ClientState.FAILED)
        self.assertEqual(grab_attempts, 2)
        self.assertEqual(
            injector.plans,
            [client.InjectionPlan("workstation text ", "ctrl_enter")],
        )
        self.assertFalse(x11.dynamic)
        self.assertIn("reacquisition failed", notifier.history[-1][1])
        self.assertEqual(
            [message["type"] for message in transports[0].messages].count("commit"), 1
        )
        app.tick()
        self.assertEqual(len(injector.plans), 1)

    def test_window_mismatch_at_ready_sends_no_commit_and_releases_resources(self):
        app, x11, _notifier, transports, captures, _injector = self.make_app(
            FakeX11(fail_recheck=True)
        )
        app.arm()
        app.space()
        app.space()
        app.tick()
        app.tick()
        self.assertEqual(app.state, client.ClientState.FAILED)
        self.assertFalse(x11.dynamic)
        self.assertTrue(transports[0].closed)
        self.assertTrue(captures[0].stopped)
        kinds = [message["type"] for message in transports[0].messages]
        self.assertNotIn("commit", kinds)
        self.assertEqual(kinds[-1], "cancel")

    def test_one_armed_helper_reuses_sequential_requests_and_recording_cancel(self):
        app, _x11, _notifier, transports, _captures, _injector = self.make_app()
        app.arm()

        app.space()
        first_request = app.request_id
        app.space()
        app.tick()
        app.tick()
        self.assertEqual(app.state, client.ClientState.ARMED)

        app.space()
        second_request = app.request_id
        self.assertNotEqual(second_request, first_request)
        app.delete()
        self.assertEqual(app.state, client.ClientState.ARMED)
        self.assertIsNone(app.request_id)

        app.space()
        third_request = app.request_id
        self.assertNotIn(third_request, {first_request, second_request})
        app.space()
        app.tick()
        app.tick()
        self.assertEqual(app.state, client.ClientState.ARMED)
        self.assertEqual(len(transports), 1)
        self.assertEqual(
            [message["type"] for message in transports[0].messages].count("start"),
            3,
        )

    def test_blank_terminal_success_never_commits_or_manufactures_delivery(self):
        app, _x11, _notifier, transports, _captures, _injector = self.make_app(terminal="blank")
        app.arm()
        app.space()
        app.space()
        app.tick()
        app.tick()
        self.assertEqual(app.state, client.ClientState.ARMED)
        self.assertNotIn(
            "commit", [message["type"] for message in transports[0].messages]
        )

    def test_effect_uncertain_commit_is_attempted_once_then_resources_release(self):
        app, x11, _notifier, transports, _captures, _injector = self.make_app()
        app.arm()
        app.space()
        app.space()
        app.tick()
        original_exchange = transports[0].exchange
        commit_calls = 0

        def uncertain(message):
            nonlocal commit_calls
            if message["type"] == "commit":
                transports[0].messages.append(message)
                commit_calls += 1
                raise client.ClientError("commit response was lost")
            return original_exchange(message)

        transports[0].exchange = uncertain
        app.tick()
        self.assertEqual(app.state, client.ClientState.FAILED)
        self.assertEqual(commit_calls, 1)
        self.assertFalse(x11.dynamic)
        self.assertTrue(transports[0].closed)

    def test_processing_cancel_remains_nonrecording_until_terminal_completion(self):
        app, _x11, notifier, transports, _captures, _injector = self.make_app()
        app.arm()
        app.space()
        app.space()
        request_id = app.request_id
        app.delete()

        self.assertEqual(app.state, client.ClientState.PROCESSING)
        self.assertEqual(app.request_id, request_id)
        self.assertEqual(transports[0].messages[-1]["type"], "cancel")
        self.assertIn("Cancellation in progress", notifier.history[-1][1])

        app.tick()
        self.assertEqual(app.state, client.ClientState.PROCESSING)
        self.assertEqual(app.request_id, request_id)
        app.tick()
        self.assertEqual(app.state, client.ClientState.ARMED)
        self.assertIsNone(app.request_id)

        app.space()
        self.assertEqual(app.state, client.ClientState.RECORDING)
        self.assertEqual(len(transports), 1)

    def test_right_control_cancels_processing_reaps_children_and_returns_off(self):
        app, x11, _notifier, transports, captures, _injector = self.make_app()
        app.arm()
        app.space()
        app.space()
        app.right_control()
        self.assertEqual(app.state, client.ClientState.OFF)
        self.assertTrue(captures[0].stopped)
        self.assertTrue(transports[0].closed)
        self.assertFalse(x11.dynamic)

    def test_ssh_replacement_and_capture_failure_are_visible_nonrecording_failures(self):
        app, x11, _notifier, transports, captures, _injector = self.make_app()
        app.arm()
        transports[0].exited = True
        app.tick()
        self.assertEqual(app.state, client.ClientState.FAILED)
        self.assertFalse(x11.dynamic)

        app.arm()
        app.space()
        captures[-1].fail_callback("microphone capture exited")
        app.tick()
        self.assertEqual(app.state, client.ClientState.FAILED)
        self.assertFalse(x11.dynamic)
        self.assertTrue(transports[-1].closed)


class ProductionPipeIntegrationTests(unittest.TestCase):
    def make_fixture(self, directory: Path, capture_body: str | None = None):
        log = directory / "events.log"
        ssh = executable(
            directory / "ssh-fake",
            f"""
            #!/usr/bin/python3
            import json, os
            log = {str(log)!r}
            def event(value):
                with open(log, "a", encoding="utf-8") as stream:
                    stream.write(value + "\\n")
            def exact(length):
                value = b""
                while len(value) < length:
                    part = os.read(0, length - len(value))
                    if not part: raise SystemExit(0)
                    value += part
                return value
            def send(value):
                payload = json.dumps(value, separators=(",", ":")).encode()
                os.write(1, len(payload).to_bytes(4, "big") + payload)
            event("ssh-start")
            request_number = 0
            active = None
            phase = None
            processing_polls = 0
            while True:
                first = os.read(0, 1)
                if not first: break
                header = first + exact(3)
                message = json.loads(exact(int.from_bytes(header, "big")))
                kind = message["type"]
                event("ssh:" + kind)
                if kind == "start":
                    if active is not None: raise SystemExit(3)
                    request_number += 1
                    active = f"r-real-{{request_number}}"
                    phase = "recording"
                    processing_polls = 0
                    send({{"version":1,"status":"recording","request_id":active}})
                    continue
                request = message.get("request_id")
                if request != active: raise SystemExit(4)
                if kind == "audio" and phase == "recording":
                    send({{"version":1,"status":"accepted","request_id":request}})
                elif kind == "finish" and phase == "recording":
                    phase = "processing"
                    send({{"version":1,"status":"processing","request_id":request}})
                elif kind == "status" and phase == "processing":
                    processing_polls += 1
                    status = "processing" if processing_polls == 1 else "ready"
                    if status == "ready": phase = "ready"
                    send({{"version":1,"status":status,"request_id":request}})
                elif kind == "commit" and phase == "ready":
                    send({{"version":1,"status":"succeeded","request_id":request}})
                    active = None
                    phase = None
                elif kind == "cancel" and phase == "processing":
                    phase = "cancelling"
                    processing_polls = 0
                    send({{"version":1,"status":"cancelling","request_id":request}})
                elif kind == "status" and phase == "cancelling":
                    processing_polls += 1
                    status = "cancelling" if processing_polls == 1 else "cancelled"
                    send({{"version":1,"status":status,"request_id":request}})
                    if status == "cancelled":
                        active = None
                        phase = None
                elif kind == "cancel":
                    send({{"version":1,"status":"cancelled","request_id":request}})
                    active = None
                    phase = None
                else: raise SystemExit(5)
            event("ssh-exit")
            """,
        )
        if capture_body is None:
            capture_body = f"""
                #!/usr/bin/python3
                import os, time
                with open({str(log)!r}, "a", encoding="utf-8") as stream:
                    stream.write("capture-start\\n")
                while True:
                    os.write(1, b"\\x01\\x00" * 160)
                    time.sleep(0.01)
            """
        capture = executable(directory / "pw-record", capture_body)
        configured = config(
            ssh_path=str(ssh),
            notify_send_path="/bin/true",
            capture_argv=[
                str(capture),
                "--rate",
                "16000",
                "--channels",
                "1",
                "--format",
                "s16",
                "-",
            ],
        )
        return configured, log

    def test_full_production_client_crosses_ssh_capture_ready_commit_without_binder(self):
        with tempfile.TemporaryDirectory() as temporary:
            configured, log = self.make_fixture(Path(temporary))
            x11 = FakeX11()
            notifier = FakeNotifier()
            transports = []
            captures = []

            def transport_factory(value):
                transport = client.ProtocolTransport(value)
                transports.append(transport)
                return transport

            def capture_factory(value, send, fail):
                capture = client.MicrophoneCapture(value, send, fail)
                captures.append(capture)
                return capture

            app = client.LaptopApplication(
                configured, x11, notifier, transport_factory, capture_factory
            )
            app.arm()
            self.assertEqual(len(transports), 1)

            def start_and_wait_for_audio():
                prior_audio = (
                    log.read_text(encoding="utf-8").count("ssh:audio")
                    if log.exists()
                    else 0
                )
                app.space()
                self.assertEqual(app.state, client.ClientState.RECORDING)
                request_id = app.request_id
                deadline = time.monotonic() + 2
                while log.read_text(encoding="utf-8").count("ssh:audio") <= prior_audio:
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.01)
                return request_id

            completed = []
            for _ in range(2):
                completed.append(start_and_wait_for_audio())
                app.space()
                self.assertEqual(app.state, client.ClientState.PROCESSING)
                app.tick()
                app.tick()
                self.assertEqual(app.state, client.ClientState.ARMED)

            cancelled = start_and_wait_for_audio()
            app.delete()
            self.assertEqual(app.state, client.ClientState.ARMED)
            self.assertIsNone(app.request_id)

            after_cancel = start_and_wait_for_audio()
            app.space()
            app.tick()
            app.tick()
            self.assertEqual(app.state, client.ClientState.ARMED)
            self.assertEqual(len({*completed, cancelled, after_cancel}), 4)
            self.assertEqual(len(transports), 1)

            ssh_process = transports[0].process
            self.assertIsNotNone(ssh_process)
            app.close()
            self.assertIsNotNone(ssh_process.poll())
            self.assertFalse(x11.dynamic)

            events = log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(events.count("ssh-start"), 1)
            self.assertEqual(events.count("ssh:start"), 4)
            self.assertEqual(events.count("ssh:cancel"), 1)
            self.assertEqual(events.count("ssh:commit"), 3)
            self.assertFalse(any("bind" in event for event in events))
            start = events.index("ssh:start")
            capture_start = events.index("capture-start")
            audio = events.index("ssh:audio")
            finish = events.index("ssh:finish")
            ready_poll = max(
                index
                for index, event in enumerate(events[: events.index("ssh:commit")])
                if event == "ssh:status"
            )
            commit = events.index("ssh:commit")
            self.assertLess(start, capture_start)
            self.assertLess(capture_start, audio)
            self.assertLess(audio, finish)
            self.assertLess(finish, ready_poll)
            self.assertLess(ready_poll, commit)

    def test_truncated_real_capture_fails_and_reaps_both_children(self):
        with tempfile.TemporaryDirectory() as temporary:
            body = """
                #!/usr/bin/python3
                import os
                os.write(1, b"x")
            """
            configured, _log = self.make_fixture(Path(temporary), body)
            x11 = FakeX11()
            transports = []
            captures = []

            def transport_factory(value):
                item = client.ProtocolTransport(value)
                transports.append(item)
                return item

            def capture_factory(value, send, fail):
                item = client.MicrophoneCapture(value, send, fail)
                captures.append(item)
                return item

            app = client.LaptopApplication(
                configured, x11, FakeNotifier(), transport_factory, capture_factory
            )
            app.arm()
            ssh_process = transports[0].process
            self.assertIsNotNone(ssh_process)
            app.space()
            deadline = time.monotonic() + 2
            diagnostics = io.StringIO()
            with contextlib.redirect_stderr(diagnostics):
                while app.state != client.ClientState.FAILED:
                    app.tick()
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.01)
            self.assertIn("partial s16le sample", diagnostics.getvalue())
            self.assertFalse(x11.dynamic)
            self.assertIsNone(transports[0].process)
            self.assertIsNotNone(ssh_process.poll())
            self.assertIsNone(captures[0].process)

    def test_laptop_source_has_no_asr_runtime_or_model_dependency(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in ("import torch", "import onnx", "import whisper", "subprocess.*handy"):
            self.assertNotRegex(source, forbidden)
        self.assertNotIn("--transcribe-file", source)


if __name__ == "__main__":
    unittest.main()
