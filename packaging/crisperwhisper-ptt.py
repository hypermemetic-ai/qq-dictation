#!/usr/bin/env python3
"""Minimal X11 hold-to-talk sidecar for CrisperWhisper and Herdr.

File and benchmark modes deliberately avoid X11, Herdr, and microphone setup.
The CrisperWhisper and python-xlib imports remain lazy so argument validation
and those non-PTT seams can be exercised in a minimal Python environment.
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence, TextIO


DEFAULT_KEY = "F9"
HERDR_WINDOW_TITLE = "herdr"
TARGET_TIMEOUT_SECONDS = 2.0
DELIVERY_TIMEOUT_SECONDS = 2.0
RECORDER_STARTUP_SECONDS = 0.1
RECORDER_STOP_TIMEOUT_SECONDS = 5.0
RECORDER_KILL_TIMEOUT_SECONDS = 2.0
X11_POLL_SECONDS = 0.25
_TERMINATION_REQUESTED = False


class SidecarError(RuntimeError):
    """A fail-closed operational error suitable for one-line logging."""


@dataclass(frozen=True)
class CapturedTarget:
    """The Herdr executable and pane fixed at recording start."""

    herdr: str
    pane_id: str


@dataclass
class Recorder:
    """One live ffmpeg process and its unique temporary output."""

    process: Any
    path: Path


@dataclass(frozen=True)
class TranscriptionMeasurement:
    text: str
    duration: float | None
    processing_time: float | None
    wall_time: float


class PTTKeyState:
    """Small press/release state machine; the caller performs work synchronously."""

    def __init__(self) -> None:
        self.holding = False

    def press(self) -> bool:
        """Accept the first press and ignore repeated presses while held."""
        if self.holding:
            return False
        self.holding = True
        return True

    def cancel(self) -> None:
        """Return to idle when key-down setup fails before recording starts."""
        self.holding = False

    def release(self, *, autorepeat: bool = False) -> bool:
        """Accept a real release, preserving state for an autorepeat pair."""
        if autorepeat:
            return False
        was_holding = self.holding
        self.holding = False
        return was_holding


def positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Hold an X11 key to transcribe into the Herdr pane focused at "
            "key-down, or transcribe/benchmark existing non-private audio."
        )
    )
    parser.add_argument(
        "--backend",
        choices=("ct2", "transformers"),
        required=True,
        help="explicit backend selected from owner measurements",
    )
    parser.add_argument("--model", default="turbo")
    parser.add_argument("--device", choices=("cpu", "cuda", "auto"), default="auto")
    parser.add_argument("--compute-type", default="float16")
    parser.add_argument("--key", default=DEFAULT_KEY, help="X11 keysym (default: F9)")
    parser.add_argument("--language", default="en")
    parser.add_argument(
        "--runtime-dir",
        type=Path,
        default=(
            Path(os.environ["CRISPERWHISPER_RUNTIME_DIR"])
            if os.environ.get("CRISPERWHISPER_RUNTIME_DIR")
            else None
        ),
        help=(
            "existing absolute directory for temporary PTT WAVs "
            "(or CRISPERWHISPER_RUNTIME_DIR)"
        ),
    )
    parser.add_argument("--audio-input-format", default="pulse")
    parser.add_argument("--audio-input", default="default")
    parser.add_argument(
        "--submit",
        action="store_true",
        help="send Enter only after text is delivered successfully",
    )

    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--transcribe-file",
        metavar="PATH",
        type=Path,
        help="transcribe one existing file and print stable JSON",
    )
    modes.add_argument(
        "--benchmark",
        metavar="PATH",
        type=Path,
        nargs="+",
        help="benchmark existing files with one warm model and print stable JSON",
    )
    parser.add_argument(
        "--runs",
        type=positive_int,
        default=None,
        help="positive runs per benchmark file (default: 1)",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse and validate all local state before any third-party import."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.benchmark is None and args.runs is not None:
        parser.error("--runs is only valid with --benchmark")
    if args.benchmark is not None:
        args.runs = args.runs or 1
        missing = [str(path) for path in args.benchmark if not path.is_file()]
        if missing:
            parser.error(f"benchmark input is not a file: {missing[0]}")
    elif args.transcribe_file is not None:
        if not args.transcribe_file.is_file():
            parser.error(f"transcription input is not a file: {args.transcribe_file}")
    else:
        runtime_dir = args.runtime_dir
        if runtime_dir is None:
            parser.error(
                "PTT mode requires --runtime-dir or CRISPERWHISPER_RUNTIME_DIR"
            )
        if not runtime_dir.is_absolute():
            parser.error("PTT runtime directory must be an absolute path")
        if runtime_dir.is_symlink():
            parser.error("PTT runtime directory must not be a symbolic link")
        if not runtime_dir.is_dir():
            parser.error(f"PTT runtime directory is not a directory: {runtime_dir}")
        try:
            runtime_dir_writable = os.access(runtime_dir, os.W_OK | os.X_OK)
        except OSError as exc:
            parser.error(f"failed to inspect PTT runtime directory: {exc}")
        if not runtime_dir_writable:
            parser.error(f"PTT runtime directory is not writable: {runtime_dir}")

    return args


def load_model(args: argparse.Namespace) -> Any:
    """Lazily construct exactly one explicitly configured model."""
    try:
        runtime = importlib.import_module("crisperwhisper")
        model_class = runtime.CrisperWhisperModel
    except (AttributeError, ImportError, OSError) as exc:
        raise SidecarError(f"CrisperWhisper runtime is unavailable: {exc}") from exc

    try:
        return model_class(
            args.model,
            backend=args.backend,
            device=args.device,
            compute_type=args.compute_type,
        )
    except Exception as exc:
        raise SidecarError(f"failed to load CrisperWhisper model: {exc}") from exc


def parse_focused_pane_id(snapshot: str | bytes) -> str:
    """Return the focused pane only for the required snapshot shape."""
    try:
        value = json.loads(snapshot)
        pane_id = value["result"]["snapshot"]["focused_pane_id"]
    except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError) as exc:
        raise SidecarError(
            "herdr snapshot did not contain result.snapshot.focused_pane_id"
        ) from exc
    if not isinstance(pane_id, str) or not pane_id or not pane_id.strip():
        raise SidecarError("herdr snapshot focused pane ID is not a non-empty string")
    return pane_id


def _run_captured(
    argv: list[str],
    *,
    timeout: float,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    try:
        return run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SidecarError(f"{Path(argv[0]).name} timed out") from exc
    except OSError as exc:
        raise SidecarError(f"failed to run {Path(argv[0]).name}: {exc}") from exc


def capture_target(
    *,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    timeout: float = TARGET_TIMEOUT_SECONDS,
) -> CapturedTarget:
    """Capture the exact Herdr pane focused at key-down, or fail closed."""
    xdotool = which("xdotool")
    herdr = which("herdr")
    if not xdotool:
        raise SidecarError("xdotool executable was not found on PATH")
    if not herdr:
        raise SidecarError("herdr executable was not found on PATH")

    title_result = _run_captured(
        [xdotool, "getactivewindow", "getwindowname"],
        timeout=timeout,
        run=run,
    )
    if title_result.returncode != 0:
        raise SidecarError("xdotool could not identify the active window")
    # xdotool terminates its one title with a newline. Remove only line endings:
    # accepting surrounding spaces would no longer be an exact title match.
    title = title_result.stdout.rstrip("\r\n")
    if title != HERDR_WINDOW_TITLE:
        raise SidecarError("active X11 window title is not exactly 'herdr'")

    snapshot_result = _run_captured(
        [herdr, "api", "snapshot"],
        timeout=timeout,
        run=run,
    )
    if snapshot_result.returncode != 0:
        raise SidecarError("herdr api snapshot failed")
    return CapturedTarget(herdr=herdr, pane_id=parse_focused_pane_id(snapshot_result.stdout))


def collapse_newlines(text: str) -> str:
    """Collapse each CR/LF sequence to one safe space for PTY delivery."""
    return re.sub(r"[\r\n]+", " ", text)


def deliver_text(
    target: CapturedTarget,
    text: str,
    *,
    submit: bool,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    timeout: float = DELIVERY_TIMEOUT_SECONDS,
) -> None:
    """Deliver text as one argv item and optionally submit after success."""
    safe_text = collapse_newlines(text)
    if not safe_text.strip():
        raise SidecarError("transcription was empty")

    delivered = _run_captured(
        [target.herdr, "pane", "send-text", target.pane_id, safe_text],
        timeout=timeout,
        run=run,
    )
    if delivered.returncode != 0:
        raise SidecarError("herdr pane send-text failed")
    if not submit:
        return

    submitted = _run_captured(
        [target.herdr, "pane", "send-keys", target.pane_id, "enter"],
        timeout=timeout,
        run=run,
    )
    if submitted.returncode != 0:
        raise SidecarError("herdr pane send-keys failed")


def ffmpeg_argv(
    ffmpeg: str,
    audio_input_format: str,
    audio_input: str,
    output_path: Path,
) -> list[str]:
    """Build the fixed mono, 16 kHz WAV recorder invocation."""
    return [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        audio_input_format,
        "-i",
        audio_input,
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        "-y",
        str(output_path),
    ]


def _remove_temp(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise SidecarError(f"failed to remove temporary audio: {exc}") from exc


def _best_effort_remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _cleanup_failed_recorder_start(process: Any | None, path: Path) -> None:
    if process is not None:
        try:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=RECORDER_KILL_TIMEOUT_SECONDS)
        except Exception:
            pass
    _best_effort_remove(path)


def start_recorder(
    runtime_dir: Path,
    audio_input_format: str,
    audio_input: str,
    *,
    ffmpeg: str,
    popen: Callable[..., Any] = subprocess.Popen,
    sleep: Callable[[float], None] = time.sleep,
) -> Recorder:
    """Create one unique WAV and start ffmpeg, cleaning up startup failures."""
    try:
        fd, path_text = tempfile.mkstemp(
            prefix="crisperwhisper-", suffix=".wav", dir=runtime_dir
        )
    except OSError as exc:
        raise SidecarError(f"failed to create temporary audio: {exc}") from exc
    os.close(fd)
    path = Path(path_text)

    process = None
    try:
        process = popen(
            ffmpeg_argv(ffmpeg, audio_input_format, audio_input, path),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            start_new_session=True,
        )
        sleep(RECORDER_STARTUP_SECONDS)
        returncode = process.poll()
        if returncode is not None:
            raise SidecarError(f"ffmpeg exited during startup with status {returncode}")
        return Recorder(process=process, path=path)
    except (SidecarError, GeneratorExit, KeyboardInterrupt, SystemExit):
        _cleanup_failed_recorder_start(process, path)
        raise
    except Exception as exc:
        _cleanup_failed_recorder_start(process, path)
        raise SidecarError(f"failed to start ffmpeg: {exc}") from exc


def _kill_and_reap(process: Any) -> None:
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=RECORDER_KILL_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _validate_wav(path: Path) -> None:
    try:
        if not path.is_file() or path.stat().st_size <= 44:
            raise SidecarError("ffmpeg did not produce usable audio")
        with wave.open(str(path), "rb") as audio:
            if (
                audio.getnchannels() != 1
                or audio.getframerate() != 16000
                or audio.getnframes() <= 0
            ):
                raise SidecarError("ffmpeg output is not a usable mono 16 kHz WAV")
    except (OSError, EOFError, wave.Error) as exc:
        raise SidecarError(f"ffmpeg output is not a usable WAV: {exc}") from exc


def stop_recorder(recorder: Recorder) -> Path:
    """Stop ffmpeg gracefully, then require a successful, usable WAV."""
    process = recorder.process
    try:
        try:
            process.send_signal(signal.SIGINT)
        except OSError as exc:
            _kill_and_reap(process)
            raise SidecarError(f"failed to signal ffmpeg: {exc}") from exc

        try:
            returncode = process.wait(timeout=RECORDER_STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as exc:
            _kill_and_reap(process)
            raise SidecarError("ffmpeg did not stop before the timeout") from exc
        # ffmpeg reports 255 after a graceful SIGINT even when it finalizes a
        # valid WAV. Accept only that documented interrupt status or success;
        # every other nonzero result remains a recorder failure.
        if returncode not in (0, 255):
            raise SidecarError(f"ffmpeg exited with status {returncode}")
        _validate_wav(recorder.path)
        return recorder.path
    except BaseException:
        try:
            if process.poll() is None:
                _kill_and_reap(process)
        except OSError:
            _kill_and_reap(process)
        _best_effort_remove(recorder.path)
        raise


def abort_recorder(recorder: Recorder) -> None:
    """Best-effort bounded cleanup for Ctrl-C and termination paths."""
    process = recorder.process
    try:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired as _timeout:
                _kill_and_reap(process)
    except OSError:
        _kill_and_reap(process)
    finally:
        _best_effort_remove(recorder.path)


def _optional_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _wav_duration(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as audio:
            rate = audio.getframerate()
            if rate <= 0:
                return None
            return audio.getnframes() / rate
    except (OSError, EOFError, wave.Error):
        return None


def transcribe_once(
    model: Any,
    path: Path,
    language: str,
    *,
    clock: Callable[[], float] = time.perf_counter,
) -> TranscriptionMeasurement:
    """Transcribe once in intended mode and measure owner-observed wall time."""
    started = clock()
    try:
        result = model.transcribe(str(path), language=language, mode="intended")
    except Exception as exc:
        raise SidecarError(f"transcription failed: {exc}") from exc
    wall_time = max(0.0, clock() - started)

    text = getattr(result, "text", None)
    if not isinstance(text, str) or not text.strip():
        raise SidecarError("transcription was empty")
    duration = _optional_number(getattr(result, "duration", None))
    if duration is None:
        duration = _wav_duration(path)
    processing_time = _optional_number(getattr(result, "processing_time", None))
    return TranscriptionMeasurement(text, duration, processing_time, wall_time)


def _measurement_record(measurement: TranscriptionMeasurement) -> dict[str, Any]:
    duration = measurement.duration
    package_rtf = None
    wall_rtf = None
    if duration is not None and duration > 0:
        wall_rtf = measurement.wall_time / duration
        if measurement.processing_time is not None:
            package_rtf = measurement.processing_time / duration
    return {
        "audio_duration_seconds": duration,
        "package_processing_time_seconds": measurement.processing_time,
        "real_time_factor": package_rtf,
        "wall_real_time_factor": wall_rtf,
        "wall_time_seconds": measurement.wall_time,
    }


def _write_json(value: Any, stdout: TextIO) -> None:
    json.dump(value, stdout, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    stdout.write("\n")
    stdout.flush()


def run_file_mode(
    args: argparse.Namespace,
    *,
    model: Any | None = None,
    stdout: TextIO = sys.stdout,
    clock: Callable[[], float] = time.perf_counter,
) -> None:
    """Run one file without initializing X11, Herdr, or the recorder."""
    warm_model = model if model is not None else load_model(args)
    measurement = transcribe_once(
        warm_model, args.transcribe_file, args.language, clock=clock
    )
    record = {
        "backend": args.backend,
        "file": str(args.transcribe_file),
        "model": args.model,
        "text": measurement.text,
        **_measurement_record(measurement),
    }
    _write_json(record, stdout)


def run_benchmark_mode(
    args: argparse.Namespace,
    *,
    model: Any | None = None,
    stdout: TextIO = sys.stdout,
    clock: Callable[[], float] = time.perf_counter,
) -> None:
    """Benchmark deterministic file/run records while reusing one warm model."""
    warm_model = model if model is not None else load_model(args)
    records: list[dict[str, Any]] = []
    for path in args.benchmark:
        for run_number in range(1, args.runs + 1):
            measurement = transcribe_once(
                warm_model, path, args.language, clock=clock
            )
            records.append(
                {
                    "file": str(path),
                    "run": run_number,
                    "text": measurement.text,
                    **_measurement_record(measurement),
                }
            )
    _write_json(
        {"backend": args.backend, "model": args.model, "records": records}, stdout
    )


def finish_recording(
    recorder: Recorder,
    target: CapturedTarget,
    model: Any,
    args: argparse.Namespace,
    *,
    stop: Callable[[Recorder], Path] = stop_recorder,
    deliver: Callable[..., None] = deliver_text,
    clock: Callable[[], float] = time.perf_counter,
) -> None:
    """Stop, transcribe, and deliver one recording, always deleting its WAV."""
    try:
        path = stop(recorder)
        measurement = transcribe_once(model, path, args.language, clock=clock)
        deliver(target, measurement.text, submit=args.submit)
    finally:
        _remove_temp(recorder.path)


def is_autorepeat_pair(release_event: Any, following_event: Any, keycode: int, key_press: int) -> bool:
    """Recognize X11's release/press pair for one autorepeated key."""
    return (
        following_event is not None
        and following_event.type == key_press
        and following_event.detail == keycode
        and following_event.time == release_event.time
    )


class _X11Grab:
    def __init__(self, dpy: Any, root: Any, keycode: int, modifiers: tuple[int, ...]):
        self.dpy = dpy
        self.root = root
        self.keycode = keycode
        self.modifiers = modifiers

    def close(self) -> None:
        for modifiers in self.modifiers:
            try:
                self.root.ungrab_key(self.keycode, modifiers)
            except Exception:
                pass
        try:
            self.dpy.sync()
            self.dpy.close()
        except Exception:
            pass


def _grab_x11_key(key: str) -> tuple[_X11Grab, Any]:
    try:
        X = importlib.import_module("Xlib.X")
        XK = importlib.import_module("Xlib.XK")
        display = importlib.import_module("Xlib.display")
        error = importlib.import_module("Xlib.error")
    except (ImportError, OSError) as exc:
        raise SidecarError(f"python-xlib runtime is unavailable: {exc}") from exc

    try:
        dpy = display.Display()
    except Exception as exc:
        raise SidecarError(f"failed to open the X11 display: {exc}") from exc
    root = dpy.screen().root
    keycode = dpy.keysym_to_keycode(XK.string_to_keysym(key))
    if keycode == 0:
        dpy.close()
        raise SidecarError(f"X11 keysym {key!r} is not in the active keymap")

    modifiers = (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask)
    grab = _X11Grab(dpy, root, keycode, modifiers)
    try:
        for lock_state in modifiers:
            root.grab_key(
                keycode,
                lock_state,
                True,
                X.GrabModeAsync,
                X.GrabModeAsync,
            )
        root.change_attributes(event_mask=X.KeyPressMask | X.KeyReleaseMask)
        dpy.sync()
    except error.BadAccess as exc:
        grab.close()
        raise SidecarError(f"X11 keysym {key!r} is already grabbed") from exc
    except Exception as exc:
        grab.close()
        raise SidecarError(f"failed to grab X11 keysym {key!r}: {exc}") from exc
    return grab, X


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", file=sys.stderr, flush=True)


def wait_for_x11_event(
    grab: _X11Grab,
    buffered: list[Any],
    *,
    select_fn: Callable[..., tuple[list[Any], list[Any], list[Any]]] = select.select,
) -> Any | None:
    """Wait briefly for one event so termination signals cannot strand cleanup."""
    if buffered:
        return buffered.pop(0)
    if grab.dpy.pending_events():
        return grab.dpy.next_event()
    try:
        readable, _, _ = select_fn(
            [grab.dpy.fileno()], [], [], X11_POLL_SECONDS
        )
    except OSError as exc:
        raise SidecarError(f"failed while waiting for an X11 event: {exc}") from exc
    if not readable:
        return None
    return grab.dpy.next_event()


def run_ptt_mode(args: argparse.Namespace) -> None:
    """Run the synchronous single-flight X11 PTT event loop."""
    global _TERMINATION_REQUESTED
    _TERMINATION_REQUESTED = False
    # Loading comes before both the key grab and any readiness claim.
    model = load_model(args)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SidecarError("ffmpeg executable was not found on PATH")
    grab, X = _grab_x11_key(args.key)

    state = PTTKeyState()
    active: tuple[Recorder, CapturedTarget] | None = None
    buffered: list[Any] = []
    log(f"ready — hold {args.key}; backend={args.backend}; model={args.model}")

    try:
        while not _TERMINATION_REQUESTED:
            event = wait_for_x11_event(grab, buffered)
            if event is None:
                continue
            if event.type not in (X.KeyPress, X.KeyRelease):
                continue
            if event.detail != grab.keycode:
                continue

            if event.type == X.KeyPress:
                if not state.press():
                    continue
                try:
                    target = capture_target()
                    recorder = start_recorder(
                        args.runtime_dir,
                        args.audio_input_format,
                        args.audio_input,
                        ffmpeg=ffmpeg,
                    )
                    active = (recorder, target)
                    log("recording started")
                except SidecarError as exc:
                    state.cancel()
                    log(f"ERROR: {exc}")
                continue

            grab.dpy.sync()
            following = None
            if buffered:
                following = buffered.pop(0)
            elif grab.dpy.pending_events():
                following = grab.dpy.next_event()
            autorepeat = is_autorepeat_pair(
                event, following, grab.keycode, X.KeyPress
            )
            if following is not None and not autorepeat:
                buffered.insert(0, following)
            if not state.release(autorepeat=autorepeat):
                continue

            if active is None:
                log("ERROR: recording state was missing at key release")
                continue
            recorder, target = active
            active = None
            log("recording stopped; transcribing")
            try:
                finish_recording(recorder, target, model, args)
                log("transcription delivered")
            except SidecarError as exc:
                log(f"ERROR: {exc}")
    finally:
        if active is not None:
            abort_recorder(active[0])
        grab.close()


def _termination_requested(_signum: int, _frame: Any) -> None:
    global _TERMINATION_REQUESTED
    _TERMINATION_REQUESTED = True


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    ptt_mode = args.transcribe_file is None and args.benchmark is None
    previous_sigterm: Any = None
    if ptt_mode:
        previous_sigterm = signal.signal(signal.SIGTERM, _termination_requested)
    try:
        if args.transcribe_file is not None:
            run_file_mode(args)
        elif args.benchmark is not None:
            run_benchmark_mode(args)
        else:
            run_ptt_mode(args)
        return 0
    except SidecarError as exc:
        log(f"ERROR: {exc}")
        return 1
    except KeyboardInterrupt:
        log("stopping")
        return 130
    finally:
        if ptt_mode:
            signal.signal(signal.SIGTERM, previous_sigterm)


if __name__ == "__main__":
    raise SystemExit(main())
