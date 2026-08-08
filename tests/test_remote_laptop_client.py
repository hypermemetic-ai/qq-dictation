"""Hermetic tests for the production Linux/X11 remote laptop client."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import stat
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
        "herdr_prefix": "ctrl+b",
        "capture_argv": list(client.DEFAULT_CAPTURE_ARGV),
        "ssh_path": "/usr/bin/ssh",
        "remote_helper": "~/.local/bin/handy-remote-stream.py",
        "xdotool_path": "/usr/bin/xdotool",
        "notify_send_path": "/usr/bin/notify-send",
        "binder_key": "alt+d",
    }
    raw.update(updates)
    return client.ClientConfig.from_mapping(raw)


class ConfigAndProtocolTests(unittest.TestCase):
    def test_config_requires_exact_window_facts_and_safe_transport_values(self):
        self.assertEqual(config().capture_argv[-1], "-")
        for update, message in [
            ({"ssh_host": "host; touch /tmp/no"}, "safe SSH"),
            ({"remote_helper": "helper;bad"}, "unsafe shell"),
            ({"binder_key": "alt+x"}, r"reserved alt\+d"),
            ({"herdr_prefix": "b"}, "modified key chord"),
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
                    "herdr_prefix": "ctrl+b",
                    "capture_argv": list(client.DEFAULT_CAPTURE_ARGV),
                    "password": "not allowed",
                }
            )

    def test_protocol_framing_version_and_request_matching_are_strict(self):
        frame = client.encode_message({"type": "status", "version": 1})
        self.assertEqual(int.from_bytes(frame[:4], "big"), len(frame) - 4)
        good = client.decode_response(
            b'{"version":1,"status":"bound","request_id":"r1","pane_id":"wA:p1"}'
        )
        self.assertEqual(
            client.validate_response(good, {"bound"}, "r1")["pane_id"], "wA:p1"
        )
        for payload, message in [
            (b'{"version":2,"status":"pending","request_id":"r"}', "version"),
            (b'{"version":1,"status":"pending","request_id":"r","extra":1}', "unknown"),
            (b'{"version":1,"status":"error","error":"refused"}', "refused"),
            (b"[]", "non-object"),
        ]:
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(client.ClientError, message):
                    client.decode_response(payload)
        with self.assertRaisesRegex(client.ClientError, "stale or different"):
            client.validate_response(good, {"bound"}, "r2")

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
    def __init__(self, fail_recheck=False):
        self.identity = client.WindowIdentity(
            1234, "operator remote herdr", "com.mitchellh.ghostty"
        )
        self.dynamic = False
        self.released = 0
        self.injected = []
        self.fail_recheck = fail_recheck
        self.rechecks = 0

    def grab_dynamic(self):
        self.dynamic = True

    def release_dynamic(self):
        if self.dynamic:
            self.released += 1
        self.dynamic = False

    def active_identity(self):
        return self.identity

    def require_same_active(self, identity):
        self.rechecks += 1
        if self.fail_recheck:
            raise client.ClientError("active Ghostty window changed")
        if identity != self.identity:
            raise client.ClientError("wrong identity")

    def inject_binder(self, identity):
        self.require_same_active(identity)
        self.injected.append(identity)


class FakeTransport:
    def __init__(self, _config, terminal="succeeded"):
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
            self.phase = "pending"
            self.processing_polls = 0
            return {
                "version": 1,
                "status": "pending",
                "request_id": self.active_request,
            }

        request = message.get("request_id")
        if request != self.active_request:
            raise client.ClientError("stale fake request")
        if kind == "status" and self.phase == "pending":
            self.phase = "recording"
            return {
                "version": 1,
                "status": "bound",
                "request_id": request,
                "pane_id": "wA:p1",
            }
        if kind == "audio" and self.phase == "recording":
            return {"version": 1, "status": "accepted", "request_id": request}
        if kind == "finish" and self.phase == "recording":
            self.phase = "processing"
            return {"version": 1, "status": "processing", "request_id": request}
        if kind == "status" and self.phase == "processing":
            self.processing_polls += 1
            status = "processing" if self.processing_polls == 1 else self.terminal
            if status in {"succeeded", "failed", "cancelled"}:
                self.active_request = None
                self.phase = None
            return {"version": 1, "status": status, "request_id": request}
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
    def make_app(self, x11=None, terminal="succeeded"):
        x11 = x11 or FakeX11()
        notifier = FakeNotifier()
        transports = []
        captures = []

        def transport_factory(configured):
            value = FakeTransport(configured, terminal)
            transports.append(value)
            return value

        def capture_factory(configured, send, fail):
            value = FakeCapture(configured, send, fail)
            captures.append(value)
            return value

        app = client.LaptopApplication(
            config(), x11, notifier, transport_factory, capture_factory
        )
        return app, x11, notifier, transports, captures

    def test_failed_dynamic_grab_never_starts_ssh_and_remains_nonrecording(self):
        class RefusingX11(FakeX11):
            def grab_dynamic(self):
                raise client.ClientError("Space or Delete is already grabbed")

        app, x11, _notifier, transports, _captures = self.make_app(RefusingX11())
        app.arm()
        self.assertEqual(app.state, client.ClientState.FAILED)
        self.assertFalse(x11.dynamic)
        self.assertEqual(transports, [])

    def test_arm_bind_audio_finish_and_terminal_states_are_ordered(self):
        app, x11, notifier, transports, captures = self.make_app()
        app.right_control()
        self.assertEqual(app.state, client.ClientState.ARMED)
        self.assertTrue(x11.dynamic)
        app.space()
        self.assertEqual(app.state, client.ClientState.RECORDING)
        self.assertEqual(len(x11.injected), 1)
        kinds = [message["type"] for message in transports[0].messages]
        self.assertEqual(kinds[:3], ["start", "status", "audio"])
        app.space()
        self.assertEqual(app.state, client.ClientState.PROCESSING)
        self.assertTrue(captures[0].stopped)
        app.tick()
        self.assertEqual(app.state, client.ClientState.PROCESSING)
        app.tick()
        self.assertEqual(app.state, client.ClientState.ARMED)
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

    def test_window_mismatch_after_pending_cancels_without_audio_and_releases_grabs(self):
        app, x11, _notifier, transports, captures = self.make_app(
            FakeX11(fail_recheck=True)
        )
        app.arm()
        app.space()
        self.assertEqual(app.state, client.ClientState.FAILED)
        self.assertFalse(x11.dynamic)
        self.assertTrue(transports[0].closed)
        self.assertEqual(captures, [])
        self.assertEqual(
            [message["type"] for message in transports[0].messages],
            ["start", "cancel"],
        )

    def test_one_armed_helper_reuses_sequential_requests_and_recording_cancel(self):
        app, _x11, _notifier, transports, _captures = self.make_app()
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

    def test_processing_cancel_remains_nonrecording_until_terminal_completion(self):
        app, _x11, notifier, transports, _captures = self.make_app()
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
        app, x11, _notifier, transports, captures = self.make_app()
        app.arm()
        app.space()
        app.space()
        app.right_control()
        self.assertEqual(app.state, client.ClientState.OFF)
        self.assertTrue(captures[0].stopped)
        self.assertTrue(transports[0].closed)
        self.assertFalse(x11.dynamic)

    def test_ssh_replacement_and_capture_failure_are_visible_nonrecording_failures(self):
        app, x11, _notifier, transports, captures = self.make_app()
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
            import json, os, sys
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
                    phase = "pending"
                    processing_polls = 0
                    send({{"version":1,"status":"pending","request_id":active}})
                    continue
                request = message.get("request_id")
                if request != active: raise SystemExit(4)
                if kind == "status" and phase == "pending":
                    phase = "recording"
                    send({{"version":1,"status":"bound","request_id":request,"pane_id":"wA:p1"}})
                elif kind == "audio" and phase == "recording":
                    send({{"version":1,"status":"accepted","request_id":request}})
                elif kind == "finish" and phase == "recording":
                    phase = "processing"
                    send({{"version":1,"status":"processing","request_id":request}})
                elif kind == "status" and phase == "processing":
                    processing_polls += 1
                    status = "processing" if processing_polls == 1 else "succeeded"
                    send({{"version":1,"status":status,"request_id":request}})
                    if status == "succeeded":
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
        xdotool = executable(
            directory / "xdotool-fake",
            f"""
            #!/usr/bin/python3
            import sys
            with open({str(log)!r}, "a", encoding="utf-8") as stream:
                stream.write("xdotool:" + "|".join(sys.argv[1:]) + "\\n")
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
            xdotool_path=str(xdotool),
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

    def test_full_production_client_crosses_real_ssh_capture_and_injection_pipes(self):
        with tempfile.TemporaryDirectory() as temporary:
            configured, log = self.make_fixture(Path(temporary))
            x11 = FakeX11()

            def inject(identity):
                client.inject_binder_chord(configured, identity, x11.require_same_active)
                x11.injected.append(identity)

            x11.inject_binder = inject
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
            start = events.index("ssh:start")
            chord = next(index for index, value in enumerate(events) if value.startswith("xdotool:"))
            bound_poll = events.index("ssh:status")
            capture_start = events.index("capture-start")
            audio = events.index("ssh:audio")
            finish = events.index("ssh:finish")
            self.assertLess(start, chord)
            self.assertLess(chord, bound_poll)
            self.assertLess(bound_poll, capture_start)
            self.assertLess(capture_start, audio)
            self.assertLess(audio, finish)
            chord_args = events[chord]
            self.assertIn("--window|1234", chord_args)
            self.assertIn("ctrl+b|alt+d", chord_args)

    def test_truncated_real_capture_fails_and_reaps_both_children(self):
        with tempfile.TemporaryDirectory() as temporary:
            body = """
                #!/usr/bin/python3
                import os
                os.write(1, b"x")
            """
            configured, _log = self.make_fixture(Path(temporary), body)
            x11 = FakeX11()
            x11.inject_binder = lambda identity: client.inject_binder_chord(
                configured, identity, x11.require_same_active
            )
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
