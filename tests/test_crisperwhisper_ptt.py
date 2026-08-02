"""Focused unit tests for the minimal CrisperWhisper PTT sidecar."""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "packaging" / "crisperwhisper-ptt.py"
SPEC = importlib.util.spec_from_file_location("crisperwhisper_ptt", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
ptt = importlib.util.module_from_spec(SPEC)
# dataclasses consult sys.modules while processing postponed annotations.
import sys

sys.modules[SPEC.name] = ptt
SPEC.loader.exec_module(ptt)


def completed(argv, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


def write_wav(path: Path, *, seconds: float = 0.01) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * max(1, int(16000 * seconds)))


class FakeProcess:
    def __init__(self, *, poll=None, waits=None):
        self.poll_result = poll
        self.waits = list(waits or [0])
        self.signals = []
        self.killed = False

    def poll(self):
        return self.poll_result

    def send_signal(self, sent_signal):
        self.signals.append(sent_signal)

    def wait(self, timeout):
        result = self.waits.pop(0)
        if isinstance(result, BaseException):
            raise result
        self.poll_result = result
        return result

    def kill(self):
        self.killed = True
        self.poll_result = -9


class FocusedPaneParsingTests(unittest.TestCase):
    def test_parses_valid_focused_pane(self):
        snapshot = b'{"result":{"snapshot":{"focused_pane_id":"w4G:p2"}}}'
        self.assertEqual(ptt.parse_focused_pane_id(snapshot), "w4G:p2")

    def test_rejects_missing_empty_and_wrong_shapes(self):
        invalid = (
            b"{}",
            b'{"result":{}}',
            b'{"result":{"snapshot":{}}}',
            b'{"result":{"snapshot":{"focused_pane_id":""}}}',
            b'{"result":{"snapshot":{"focused_pane_id":"   "}}}',
            b'{"result":{"snapshot":{"focused_pane_id":7}}}',
            b'{"result":{"snapshot":[]}}',
            b"not-json",
        )
        for snapshot in invalid:
            with self.subTest(snapshot=snapshot):
                with self.assertRaises(ptt.SidecarError):
                    ptt.parse_focused_pane_id(snapshot)


class TargetCaptureTests(unittest.TestCase):
    @staticmethod
    def which(name):
        return {"xdotool": "/usr/bin/xdotool", "herdr": "/opt/bin/herdr"}.get(name)

    def test_exact_title_and_snapshot_capture_use_bounded_argv(self):
        calls = []

        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            if argv[0].endswith("xdotool"):
                return completed(argv, stdout="herdr\n")
            return completed(
                argv,
                stdout='{"result":{"snapshot":{"focused_pane_id":"wA:p1"}}}',
            )

        target = ptt.capture_target(which=self.which, run=run, timeout=0.25)

        self.assertEqual(target, ptt.CapturedTarget("/opt/bin/herdr", "wA:p1"))
        self.assertEqual(
            calls[0][0],
            ["/usr/bin/xdotool", "getactivewindow", "getwindowname"],
        )
        self.assertEqual(calls[1][0], ["/opt/bin/herdr", "api", "snapshot"])
        for _, kwargs in calls:
            self.assertEqual(kwargs["timeout"], 0.25)
            self.assertFalse(kwargs["shell"])

    def test_nonexact_title_fails_before_snapshot(self):
        run = mock.Mock(return_value=completed([], stdout="herdr \n"))
        with self.assertRaisesRegex(ptt.SidecarError, "exactly"):
            ptt.capture_target(which=self.which, run=run)
        self.assertEqual(run.call_count, 1)

    def test_missing_tools_nonzero_commands_and_timeouts_fail_closed(self):
        with self.assertRaisesRegex(ptt.SidecarError, "xdotool executable"):
            ptt.capture_target(which=lambda _name: None, run=mock.Mock())

        nonzero = mock.Mock(return_value=completed([], returncode=1))
        with self.assertRaisesRegex(ptt.SidecarError, "could not identify"):
            ptt.capture_target(which=self.which, run=nonzero)

        timed_out = mock.Mock(
            side_effect=subprocess.TimeoutExpired(["xdotool"], timeout=2)
        )
        with self.assertRaisesRegex(ptt.SidecarError, "timed out"):
            ptt.capture_target(which=self.which, run=timed_out)

    def test_snapshot_failure_and_bad_shape_fail_closed(self):
        for second_result in (
            completed([], returncode=1),
            completed([], stdout='{"result":{"snapshot":{}}}'),
        ):
            responses = iter(
                [completed([], stdout="herdr\n"), second_result]
            )
            with self.subTest(second_result=second_result):
                with self.assertRaises(ptt.SidecarError):
                    ptt.capture_target(
                        which=self.which, run=lambda *_args, **_kwargs: next(responses)
                    )


class DeliveryTests(unittest.TestCase):
    def test_collapses_cr_lf_sequences(self):
        self.assertEqual(
            ptt.collapse_newlines("first\r\n\nsecond\rthird"),
            "first second third",
        )

    def test_leading_dash_is_data_and_enter_follows_success(self):
        calls = []

        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            return completed(argv)

        target = ptt.CapturedTarget("/bin/herdr", "w1:p9")
        ptt.deliver_text(target, "-not an option\r\nnext", submit=True, run=run)

        self.assertEqual(
            calls[0][0],
            ["/bin/herdr", "pane", "send-text", "w1:p9", "-not an option next"],
        )
        self.assertEqual(
            calls[1][0],
            ["/bin/herdr", "pane", "send-keys", "w1:p9", "enter"],
        )
        self.assertTrue(all(call[1]["shell"] is False for call in calls))

    def test_enter_is_not_sent_after_text_delivery_failure(self):
        run = mock.Mock(return_value=completed([], returncode=2))
        target = ptt.CapturedTarget("/bin/herdr", "w1:p9")
        with self.assertRaisesRegex(ptt.SidecarError, "send-text"):
            ptt.deliver_text(target, "hello", submit=True, run=run)
        self.assertEqual(run.call_count, 1)


class RecorderTests(unittest.TestCase):
    def test_ffmpeg_argv_has_input_mono_16khz_unique_path_and_no_shell(self):
        process = FakeProcess(poll=None, waits=[0])
        popen = mock.Mock(return_value=process)
        with tempfile.TemporaryDirectory() as directory:
            recorder = ptt.start_recorder(
                Path(directory),
                "pulse",
                "chosen-source",
                ffmpeg="/usr/bin/ffmpeg",
                popen=popen,
                sleep=lambda _seconds: None,
            )
            argv = popen.call_args.args[0]
            self.assertEqual(argv[0], "/usr/bin/ffmpeg")
            self.assertEqual(argv[argv.index("-f") + 1], "pulse")
            self.assertEqual(argv[argv.index("-i") + 1], "chosen-source")
            self.assertEqual(argv[argv.index("-ac") + 1], "1")
            self.assertEqual(argv[argv.index("-ar") + 1], "16000")
            self.assertEqual(Path(argv[-1]), recorder.path)
            self.assertEqual(recorder.path.parent, Path(directory))
            self.assertTrue(recorder.path.name.startswith("crisperwhisper-"))
            self.assertFalse(popen.call_args.kwargs["shell"])
            ptt.abort_recorder(recorder)
            self.assertFalse(recorder.path.exists())

    def test_startup_interrupt_kills_process_and_removes_temporary_wav(self):
        process = FakeProcess(poll=None, waits=[0])

        def interrupt(_seconds):
            raise KeyboardInterrupt

        with tempfile.TemporaryDirectory() as directory:
            runtime_dir = Path(directory)
            with self.assertRaises(KeyboardInterrupt):
                ptt.start_recorder(
                    runtime_dir,
                    "pulse",
                    "chosen-source",
                    ffmpeg="/usr/bin/ffmpeg",
                    popen=mock.Mock(return_value=process),
                    sleep=interrupt,
                )
            self.assertTrue(process.killed)
            self.assertEqual(list(runtime_dir.iterdir()), [])

    def test_stop_nonzero_removes_temporary_wav(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recording.wav"
            write_wav(path)
            process = FakeProcess(poll=None, waits=[7])
            with self.assertRaisesRegex(ptt.SidecarError, "status 7"):
                ptt.stop_recorder(ptt.Recorder(process, path))
            self.assertFalse(path.exists())
            self.assertEqual(process.signals, [ptt.signal.SIGINT])

    def test_stop_timeout_kills_reaps_and_removes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recording.wav"
            write_wav(path)
            process = FakeProcess(
                poll=None,
                waits=[subprocess.TimeoutExpired("ffmpeg", 5), -9],
            )
            with self.assertRaisesRegex(ptt.SidecarError, "timeout"):
                ptt.stop_recorder(ptt.Recorder(process, path))
            self.assertTrue(process.killed)
            self.assertFalse(path.exists())

    def test_success_requires_usable_mono_16khz_wav(self):
        with tempfile.TemporaryDirectory() as directory:
            good = Path(directory) / "good.wav"
            write_wav(good)
            recorder = ptt.Recorder(FakeProcess(poll=None, waits=[0]), good)
            self.assertEqual(ptt.stop_recorder(recorder), good)
            self.assertTrue(good.exists())

            interrupted = Path(directory) / "interrupted.wav"
            write_wav(interrupted)
            recorder = ptt.Recorder(FakeProcess(poll=None, waits=[255]), interrupted)
            self.assertEqual(ptt.stop_recorder(recorder), interrupted)
            self.assertTrue(interrupted.exists())

            bad = Path(directory) / "bad.wav"
            bad.write_bytes(b"not a wav")
            with self.assertRaisesRegex(ptt.SidecarError, "usable"):
                ptt.stop_recorder(
                    ptt.Recorder(FakeProcess(poll=None, waits=[0]), bad)
                )
            self.assertFalse(bad.exists())

    def test_empty_transcript_is_rejected_and_wav_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recording.wav"
            write_wav(path)
            recorder = ptt.Recorder(FakeProcess(), path)
            model = mock.Mock()
            model.transcribe.return_value = SimpleNamespace(
                text=" \n ", duration=0.01, processing_time=0.1
            )
            args = SimpleNamespace(language="en", submit=True)
            deliver = mock.Mock()

            with self.assertRaisesRegex(ptt.SidecarError, "empty"):
                ptt.finish_recording(
                    recorder,
                    ptt.CapturedTarget("/bin/herdr", "w:p"),
                    model,
                    args,
                    stop=lambda recording: recording.path,
                    deliver=deliver,
                    clock=iter([1.0, 2.0]).__next__,
                )
            self.assertFalse(path.exists())
            deliver.assert_not_called()


class FileAndBenchmarkTests(unittest.TestCase):
    def test_file_mode_calls_intended_and_emits_stable_json_without_ptt(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.wav"
            path.write_bytes(b"synthetic")
            args = SimpleNamespace(
                transcribe_file=path,
                language="en",
                backend="ct2",
                model="turbo",
            )
            model = mock.Mock()
            model.transcribe.return_value = SimpleNamespace(
                text="intended result", duration=5.0, processing_time=1.25
            )
            output = io.StringIO()

            with (
                mock.patch.object(ptt, "capture_target") as capture,
                mock.patch.object(ptt, "start_recorder") as recorder,
                mock.patch.object(ptt, "_grab_x11_key") as grab,
            ):
                ptt.run_file_mode(
                    args,
                    model=model,
                    stdout=output,
                    clock=iter([10.0, 12.0]).__next__,
                )

            model.transcribe.assert_called_once_with(
                str(path), language="en", mode="intended"
            )
            capture.assert_not_called()
            recorder.assert_not_called()
            grab.assert_not_called()
            parsed = json.loads(output.getvalue())
            self.assertEqual(parsed["text"], "intended result")
            self.assertEqual(parsed["backend"], "ct2")
            self.assertEqual(parsed["audio_duration_seconds"], 5.0)
            self.assertEqual(parsed["package_processing_time_seconds"], 1.25)
            self.assertEqual(parsed["wall_time_seconds"], 2.0)
            # Compact, key-sorted output is stable and parseable.
            self.assertEqual(output.getvalue(), json.dumps(parsed, sort_keys=True, separators=(",", ":")) + "\n")

    def test_optional_number_rejects_failed_float_conversion(self):
        class InvalidFloat(float):
            def __float__(self):
                raise ValueError("synthetic conversion failure")

        self.assertIsNone(ptt._optional_number(InvalidFloat(1.0)))

    def test_benchmark_loads_one_model_and_records_every_file_run(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / "a.wav", Path(directory) / "b.wav"]
            for path in paths:
                path.write_bytes(b"synthetic")
            args = SimpleNamespace(
                benchmark=paths,
                runs=2,
                language="en",
                backend="transformers",
                model="turbo",
            )
            model = mock.Mock()
            model.transcribe.side_effect = [
                SimpleNamespace(text=f"result-{index}", duration=2.0, processing_time=0.5)
                for index in range(4)
            ]
            output = io.StringIO()
            ticks = iter([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])

            with mock.patch.object(ptt, "load_model", return_value=model) as load:
                ptt.run_benchmark_mode(args, stdout=output, clock=ticks.__next__)

            load.assert_called_once_with(args)
            self.assertEqual(model.transcribe.call_count, 4)
            parsed = json.loads(output.getvalue())
            self.assertEqual(parsed["backend"], "transformers")
            self.assertEqual(
                [(record["file"], record["run"]) for record in parsed["records"]],
                [
                    (str(paths[0]), 1),
                    (str(paths[0]), 2),
                    (str(paths[1]), 1),
                    (str(paths[1]), 2),
                ],
            )
            for record in parsed["records"]:
                self.assertIn("wall_time_seconds", record)
                self.assertIn("package_processing_time_seconds", record)
                self.assertIn("audio_duration_seconds", record)
                self.assertIn("real_time_factor", record)
                self.assertIn("wall_real_time_factor", record)
                self.assertEqual(record["real_time_factor"], 0.25)


class CliAndStateTests(unittest.TestCase):
    def test_cli_rejects_nonpositive_runs_before_model_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.wav"
            path.write_bytes(b"synthetic")
            with (
                mock.patch.object(ptt, "load_model") as load,
                self.assertRaises(SystemExit),
                mock.patch("sys.stderr", new=io.StringIO()),
            ):
                ptt.main(
                    [
                        "--backend",
                        "ct2",
                        "--benchmark",
                        str(path),
                        "--runs",
                        "0",
                    ]
                )
            load.assert_not_called()

    def test_cli_rejects_contradictory_modes_before_model_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.wav"
            path.write_bytes(b"synthetic")
            with (
                mock.patch.object(ptt, "load_model") as load,
                self.assertRaises(SystemExit),
                mock.patch("sys.stderr", new=io.StringIO()),
            ):
                ptt.main(
                    [
                        "--backend",
                        "ct2",
                        "--transcribe-file",
                        str(path),
                        "--benchmark",
                        str(path),
                    ]
                )
            load.assert_not_called()

    def test_cli_reports_runtime_access_probe_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                mock.patch.object(ptt.os, "access", side_effect=OSError("denied")),
                self.assertRaises(SystemExit),
                mock.patch("sys.stderr", new=io.StringIO()),
            ):
                ptt.parse_args(
                    ["--backend", "ct2", "--runtime-dir", directory]
                )

    def test_press_release_state_and_autorepeat_filtering(self):
        state = ptt.PTTKeyState()
        self.assertTrue(state.press())
        self.assertFalse(state.press())
        self.assertFalse(state.release(autorepeat=True))
        self.assertTrue(state.holding)
        self.assertTrue(state.release())
        self.assertFalse(state.holding)
        self.assertFalse(state.release())

        release = SimpleNamespace(type=3, detail=42, time=100)
        repeated_press = SimpleNamespace(type=2, detail=42, time=100)
        real_press = SimpleNamespace(type=2, detail=42, time=101)
        self.assertTrue(ptt.is_autorepeat_pair(release, repeated_press, 42, 2))
        self.assertFalse(ptt.is_autorepeat_pair(release, real_press, 42, 2))

    def test_x11_event_wait_is_bounded_for_termination(self):
        next_event = mock.Mock()
        dpy = SimpleNamespace(
            pending_events=lambda: 0,
            fileno=lambda: 17,
            next_event=next_event,
        )
        grab = ptt._X11Grab(dpy, root=None, keycode=42, modifiers=())
        select_fn = mock.Mock(return_value=([], [], []))

        self.assertIsNone(
            ptt.wait_for_x11_event(grab, [], select_fn=select_fn)
        )
        select_fn.assert_called_once_with([17], [], [], ptt.X11_POLL_SECONDS)
        next_event.assert_not_called()

        buffered_event = object()
        self.assertIs(
            ptt.wait_for_x11_event(grab, [buffered_event], select_fn=select_fn),
            buffered_event,
        )

    def test_default_key_does_not_collide_with_handy_bridge(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.wav"
            path.write_bytes(b"synthetic")
            args = ptt.parse_args(
                ["--backend", "ct2", "--transcribe-file", str(path)]
            )
        self.assertEqual(args.key, "F9")
        self.assertNotEqual(args.key, "Control_R")

    def test_model_constructor_receives_explicit_configuration(self):
        args = SimpleNamespace(
            model="turbo", backend="ct2", device="cpu", compute_type="int8"
        )
        model_class = mock.Mock(return_value=object())
        fake_module = SimpleNamespace(CrisperWhisperModel=model_class)
        with mock.patch.dict(sys.modules, {"crisperwhisper": fake_module}):
            loaded = ptt.load_model(args)
        self.assertIs(loaded, model_class.return_value)
        model_class.assert_called_once_with(
            "turbo", backend="ct2", device="cpu", compute_type="int8"
        )


if __name__ == "__main__":
    unittest.main()
