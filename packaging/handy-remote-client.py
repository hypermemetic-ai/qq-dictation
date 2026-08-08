#!/usr/bin/env python3
"""Thin Linux/X11 laptop client for workstation-owned qq-dictation.

The laptop owns only X11 controls, exact-window chord injection, microphone
capture, one SSH helper process, and local status. It runs no ASR model.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import select
import signal
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from Xlib import X, XK, display
from Xlib.error import XError

PROTOCOL_VERSION = 1
MAX_PROTOCOL_FRAME_BYTES = 65_536
MAX_AUDIO_SAMPLES = 4_800
AUDIO_BYTES_PER_SAMPLE = 2
AUDIO_CHUNK_BYTES = MAX_AUDIO_SAMPLES * AUDIO_BYTES_PER_SAMPLE
BIND_TIMEOUT_SECONDS = 5.0
PROTOCOL_TIMEOUT_SECONDS = 10.0
PROCESS_STOP_SECONDS = 2.0
POLL_SECONDS = 0.1

MODE_KEY = "Control_R"
START_STOP_KEY = "space"
CANCEL_KEY = "Delete"
GRAB_MODIFIERS = (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask)

DEFAULT_CAPTURE_ARGV = [
    "/usr/bin/pw-record",
    "--rate",
    "16000",
    "--channels",
    "1",
    "--format",
    "s16",
    "-",
]
DEFAULTS = {
    "ssh_path": "/usr/bin/ssh",
    "remote_helper": "~/.local/bin/handy-remote-stream.py",
    "xdotool_path": "/usr/bin/xdotool",
    "notify_send_path": "/usr/bin/notify-send",
    "binder_key": "alt+d",
}
REQUIRED_CONFIG = {
    "ssh_host",
    "ghostty_title",
    "ghostty_class",
    "herdr_prefix",
    "capture_argv",
}
ALLOWED_CONFIG = REQUIRED_CONFIG | set(DEFAULTS)
SAFE_SSH_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@:-]{0,254}\Z")
SAFE_REMOTE_HELPER = re.compile(r"[A-Za-z0-9_./~+-]{1,512}\Z")
SAFE_CHORD = re.compile(
    r"(?:ctrl|alt|shift|super|cmd)(?:\+(?:ctrl|alt|shift|super|cmd))*\+[A-Za-z0-9?_-]+\Z"
)


class ClientError(RuntimeError):
    """A fail-closed client error suitable for local status."""


class ClientState(str, Enum):
    OFF = "off"
    ARMED = "armed"
    RECORDING = "recording"
    PROCESSING = "processing"
    FAILED = "failed"


@dataclass(frozen=True)
class ClientConfig:
    ssh_host: str
    ghostty_title: str
    ghostty_class: str
    herdr_prefix: str
    capture_argv: tuple[str, ...]
    ssh_path: str
    remote_helper: str
    xdotool_path: str
    notify_send_path: str
    binder_key: str

    @classmethod
    def from_mapping(cls, raw: object) -> "ClientConfig":
        if not isinstance(raw, dict):
            raise ClientError("configuration must be one JSON object")
        fields = set(raw)
        missing = REQUIRED_CONFIG - fields
        unknown = fields - ALLOWED_CONFIG
        if missing:
            raise ClientError(f"configuration is missing: {', '.join(sorted(missing))}")
        if unknown:
            raise ClientError(f"configuration has unknown fields: {', '.join(sorted(unknown))}")
        values = {**DEFAULTS, **raw}

        for key in (
            "ssh_host",
            "ghostty_title",
            "ghostty_class",
            "herdr_prefix",
            "ssh_path",
            "remote_helper",
            "xdotool_path",
            "notify_send_path",
            "binder_key",
        ):
            if not isinstance(values[key], str) or not values[key]:
                raise ClientError(f"{key} must be a non-empty string")
        for key in ("ghostty_title", "ghostty_class"):
            if any(ord(character) < 32 for character in values[key]):
                raise ClientError(f"{key} contains a control character")
        if not SAFE_SSH_HOST.fullmatch(values["ssh_host"]):
            raise ClientError("ssh_host is not a safe SSH host alias")
        if not SAFE_REMOTE_HELPER.fullmatch(values["remote_helper"]):
            raise ClientError("remote_helper contains unsafe shell characters")
        if not SAFE_CHORD.fullmatch(values["herdr_prefix"]):
            raise ClientError("herdr_prefix is not one explicit modified key chord")
        if values["binder_key"] != "alt+d":
            raise ClientError("binder_key must remain the reserved alt+d chord")
        for key in ("ssh_path", "xdotool_path", "notify_send_path"):
            if not Path(values[key]).is_absolute():
                raise ClientError(f"{key} must be an absolute executable path")

        capture = values["capture_argv"]
        if (
            not isinstance(capture, list)
            or not capture
            or not all(isinstance(argument, str) and argument for argument in capture)
        ):
            raise ClientError("capture_argv must be a non-empty JSON string array")
        if not Path(capture[0]).is_absolute() or Path(capture[0]).name != "pw-record":
            raise ClientError("capture_argv must invoke an absolute pw-record executable")
        expected_flags = {"--rate": "16000", "--channels": "1", "--format": "s16"}
        for flag, expected in expected_flags.items():
            positions = [index for index, argument in enumerate(capture) if argument == flag]
            if len(positions) != 1 or positions[0] + 1 >= len(capture):
                raise ClientError(f"capture_argv must declare exactly one {flag} {expected}")
            if capture[positions[0] + 1] != expected:
                raise ClientError(f"capture_argv must declare {flag} {expected}")
        if capture[-1] != "-":
            raise ClientError("capture_argv must stream raw PCM on stdout using '-' output")

        return cls(
            ssh_host=values["ssh_host"],
            ghostty_title=values["ghostty_title"],
            ghostty_class=values["ghostty_class"],
            herdr_prefix=values["herdr_prefix"],
            capture_argv=tuple(capture),
            ssh_path=values["ssh_path"],
            remote_helper=values["remote_helper"],
            xdotool_path=values["xdotool_path"],
            notify_send_path=values["notify_send_path"],
            binder_key=values["binder_key"],
        )

    @classmethod
    def load(cls, path: Path) -> "ClientConfig":
        try:
            return cls.from_mapping(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as error:
            raise ClientError(f"cannot read configuration {path}: {error}") from error

    def validate_runtime_tools(self) -> None:
        for executable in (
            self.ssh_path,
            self.xdotool_path,
            self.notify_send_path,
            self.capture_argv[0],
        ):
            path = Path(executable)
            if not path.is_file() or not os.access(path, os.X_OK):
                raise ClientError(f"required executable is unavailable: {path}")


@dataclass(frozen=True)
class WindowIdentity:
    window_id: int
    title: str
    window_class: str


class DistinctPressTracker:
    """Emit one action per physical X11 press, not per autorepeat pair."""

    def __init__(self) -> None:
        self._held = False
        self._last_release_time: int | None = None

    def on_press(self, timestamp: int) -> bool:
        if self._held or self._last_release_time == timestamp:
            return False
        self._held = True
        return True

    def on_release(self, timestamp: int) -> None:
        self._held = False
        self._last_release_time = timestamp

    def reset(self) -> None:
        self._held = False
        self._last_release_time = None


def write_all(descriptor: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise ClientError("subprocess pipe write made no progress")
        remaining = remaining[written:]


def read_exact(descriptor: int, length: int, timeout: float) -> bytes:
    deadline = time.monotonic() + timeout
    result = bytearray()
    while len(result) < length:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ClientError("workstation protocol response timed out")
        ready, _, _ = select.select([descriptor], [], [], remaining)
        if not ready:
            raise ClientError("workstation protocol response timed out")
        chunk = os.read(descriptor, length - len(result))
        if not chunk:
            raise ClientError("SSH helper closed a truncated protocol response")
        result.extend(chunk)
    return bytes(result)


def encode_message(message: dict[str, object]) -> bytes:
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    if not payload or len(payload) > MAX_PROTOCOL_FRAME_BYTES:
        raise ClientError("outgoing protocol message is outside the frame bound")
    return len(payload).to_bytes(4, "big") + payload


def decode_response(payload: bytes) -> dict[str, object]:
    try:
        response = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ClientError("workstation returned malformed JSON") from error
    if not isinstance(response, dict):
        raise ClientError("workstation returned a non-object response")
    allowed = {"version", "status", "request_id", "pane_id", "error"}
    if set(response) - allowed:
        raise ClientError("workstation response contained unknown fields")
    if type(response.get("version")) is not int or response["version"] != PROTOCOL_VERSION:
        raise ClientError("workstation response used an unsupported protocol version")
    if not isinstance(response.get("status"), str):
        raise ClientError("workstation response omitted a valid status")
    if response["status"] == "error":
        detail = response.get("error")
        if not isinstance(detail, str) or not detail:
            detail = "workstation refused the request without a reason"
        raise ClientError(detail)
    return response


def validate_response(
    response: dict[str, object],
    expected_statuses: set[str],
    request_id: str | None = None,
) -> dict[str, object]:
    status = response["status"]
    if status not in expected_statuses:
        raise ClientError(f"unexpected workstation state: {status}")
    observed_request = response.get("request_id")
    if request_id is None:
        if not isinstance(observed_request, str) or not observed_request:
            raise ClientError("workstation did not mint a request id")
    elif observed_request != request_id:
        raise ClientError("workstation response named a stale or different request")
    if status == "bound":
        pane_id = response.get("pane_id")
        if not isinstance(pane_id, str) or not pane_id:
            raise ClientError("bound response omitted its exact pane id")
    return response


class ProtocolTransport:
    """One SSH helper and one strictly serialized protocol stream."""

    def __init__(self, config: ClientConfig):
        self.config = config
        self.process: subprocess.Popen[bytes] | None = None
        self.lock = threading.Lock()
        self.closing = False

    @property
    def argv(self) -> list[str]:
        return [
            self.config.ssh_path,
            "-T",
            self.config.ssh_host,
            self.config.remote_helper,
        ]

    def start(self) -> None:
        if self.process is not None:
            raise ClientError("SSH helper is already running")
        try:
            self.process = subprocess.Popen(
                self.argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                shell=False,
                start_new_session=True,
                bufsize=0,
            )
        except OSError as error:
            raise ClientError(f"could not start SSH helper: {error}") from error

    def exchange(self, message: dict[str, object]) -> dict[str, object]:
        with self.lock:
            process = self.process
            if process is None or process.stdin is None or process.stdout is None:
                raise ClientError("SSH helper is not running")
            if process.poll() is not None:
                raise ClientError("SSH helper exited")
            try:
                write_all(process.stdin.fileno(), encode_message(message))
                length = int.from_bytes(
                    read_exact(process.stdout.fileno(), 4, PROTOCOL_TIMEOUT_SECONDS), "big"
                )
                if length <= 0 or length > MAX_PROTOCOL_FRAME_BYTES:
                    raise ClientError("workstation response frame length is outside bounds")
                payload = read_exact(
                    process.stdout.fileno(), length, PROTOCOL_TIMEOUT_SECONDS
                )
                return decode_response(payload)
            except (BrokenPipeError, OSError) as error:
                raise ClientError(f"SSH helper pipe failed: {error}") from error

    def unexpected_exit(self) -> bool:
        return (
            not self.closing
            and self.process is not None
            and self.process.poll() is not None
        )

    def close(self) -> None:
        self.closing = True
        process, self.process = self.process, None
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        reap_process(process)
        if process.stdout is not None:
            process.stdout.close()


def reap_process(process: subprocess.Popen[bytes]) -> None:
    try:
        process.wait(timeout=PROCESS_STOP_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass
    try:
        process.wait(timeout=PROCESS_STOP_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    process.wait(timeout=PROCESS_STOP_SECONDS)


def pcm_s16le_samples(chunk: bytes) -> list[int]:
    if not chunk or len(chunk) % AUDIO_BYTES_PER_SAMPLE:
        raise ClientError("microphone returned a partial s16le sample")
    if len(chunk) > AUDIO_CHUNK_BYTES:
        raise ClientError("microphone chunk exceeds the protocol audio bound")
    return list(struct.unpack(f"<{len(chunk) // 2}h", chunk))


class MicrophoneCapture:
    def __init__(
        self,
        config: ClientConfig,
        send_audio: Callable[[list[int]], None],
        fail: Callable[[str], None],
    ):
        self.config = config
        self.send_audio = send_audio
        self.fail = fail
        self.process: subprocess.Popen[bytes] | None = None
        self.thread: threading.Thread | None = None
        self.stopping = threading.Event()
        self._failure_lock = threading.Lock()
        self._failure: str | None = None

    def _report_failure(self, detail: str) -> None:
        with self._failure_lock:
            if self._failure is None:
                self._failure = detail
        self.fail(detail)

    def start(self) -> None:
        try:
            self.process = subprocess.Popen(
                list(self.config.capture_argv),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                shell=False,
                start_new_session=True,
                bufsize=0,
            )
        except OSError as error:
            raise ClientError(f"could not start microphone capture: {error}") from error
        self.thread = threading.Thread(target=self._copy_audio, daemon=True)
        self.thread.start()

    def _copy_audio(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            self._report_failure("microphone capture pipe was not initialized")
            return
        pending = bytearray()
        try:
            while True:
                chunk = os.read(process.stdout.fileno(), AUDIO_CHUNK_BYTES)
                if not chunk:
                    break
                pending.extend(chunk)
                complete = len(pending) - (len(pending) % AUDIO_BYTES_PER_SAMPLE)
                while complete:
                    length = min(complete, AUDIO_CHUNK_BYTES)
                    data = bytes(pending[:length])
                    del pending[:length]
                    complete -= length
                    self.send_audio(pcm_s16le_samples(data))
            if pending:
                raise ClientError("microphone ended with a partial s16le sample")
            return_code = process.wait()
            if not self.stopping.is_set():
                raise ClientError(
                    f"microphone capture exited unexpectedly with status {return_code}"
                )
        except (ClientError, OSError) as error:
            if not self.stopping.is_set() or "partial s16le" in str(error):
                self._report_failure(str(error))

    def stop(self) -> None:
        self.stopping.set()
        process = self.process
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
        if self.thread is not None:
            self.thread.join(timeout=PROCESS_STOP_SECONDS * 2)
        if process is not None:
            reap_process(process)
            if process.stdout is not None:
                process.stdout.close()
        if self.thread is not None and self.thread.is_alive():
            raise ClientError("microphone capture reader did not terminate")
        self.process = None
        self.thread = None
        with self._failure_lock:
            failure = self._failure
        if failure is not None:
            raise ClientError(failure)


class StatusNotifier:
    def __init__(self, executable: str):
        self.executable = executable
        self.history: list[tuple[ClientState, str]] = []

    def show(self, state: ClientState, detail: str = "") -> None:
        self.history.append((state, detail))
        summary = f"qq-dictation: {state.value}"
        try:
            subprocess.run(
                [
                    self.executable,
                    "--app-name=qq-dictation",
                    "--replace-id=25160",
                    summary,
                    detail,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                timeout=2,
                check=True,
            )
        except (OSError, subprocess.SubprocessError) as error:
            print(f"handy-remote-client: status notification failed: {error}", file=sys.stderr)


class X11Controller:
    def __init__(self, config: ClientConfig):
        self.config = config
        self.display = display.Display()
        self.root = self.display.screen().root
        self.root.change_attributes(event_mask=X.KeyPressMask | X.KeyReleaseMask)
        self.mode_keycode = self._keycode(MODE_KEY)
        self.space_keycode = self._keycode(START_STOP_KEY)
        self.cancel_keycode = self._keycode(CANCEL_KEY)
        self.dynamic_grabbed = False

    def _keycode(self, name: str) -> int:
        keycode = self.display.keysym_to_keycode(XK.string_to_keysym(name))
        if keycode == 0:
            raise ClientError(f"{name} is absent from the active X11 keymap")
        return keycode

    def _grab(self, keycodes: tuple[int, ...]) -> bool:
        attempted: list[tuple[int, int]] = []
        errors: list[Exception] = []

        def record(error, _request):
            errors.append(error)

        try:
            for keycode in keycodes:
                for modifiers in GRAB_MODIFIERS:
                    self.root.grab_key(
                        keycode,
                        modifiers,
                        True,
                        X.GrabModeAsync,
                        X.GrabModeAsync,
                        onerror=record,
                    )
                    attempted.append((keycode, modifiers))
            self.display.sync()
        except XError as error:
            errors.append(error)
        if not errors:
            return True
        for keycode, modifiers in attempted:
            self.root.ungrab_key(keycode, modifiers, onerror=lambda *_: None)
        try:
            self.display.sync()
        except XError:
            pass
        return False

    def _ungrab(self, keycodes: tuple[int, ...]) -> None:
        for keycode in keycodes:
            for modifiers in GRAB_MODIFIERS:
                self.root.ungrab_key(keycode, modifiers, onerror=lambda *_: None)
        try:
            self.display.sync()
        except XError:
            pass

    def grab_mode_key(self) -> None:
        if not self._grab((self.mode_keycode,)):
            raise ClientError("Right-Control is already grabbed by another application")

    def grab_dynamic(self) -> None:
        if self.dynamic_grabbed:
            return
        if not self._grab((self.space_keycode, self.cancel_keycode)):
            raise ClientError("Space or Delete is already grabbed by another application")
        self.dynamic_grabbed = True

    def release_dynamic(self) -> None:
        if self.dynamic_grabbed:
            self._ungrab((self.space_keycode, self.cancel_keycode))
            self.dynamic_grabbed = False

    def close(self) -> None:
        self.release_dynamic()
        self._ungrab((self.mode_keycode,))
        self.display.close()

    def active_identity(self) -> WindowIdentity:
        atom = self.display.intern_atom("_NET_ACTIVE_WINDOW")
        active = self.root.get_full_property(atom, X.AnyPropertyType)
        if active is None or not len(active.value):
            raise ClientError("X11 did not report an active window")
        window = self.display.create_resource_object("window", int(active.value[0]))
        title = window.get_wm_name()
        wm_class = window.get_wm_class()
        if not isinstance(title, str) or not wm_class or len(wm_class) != 2:
            raise ClientError("active window omitted its exact title or class")
        identity = WindowIdentity(int(active.value[0]), title, wm_class[1])
        if (
            identity.title != self.config.ghostty_title
            or identity.window_class != self.config.ghostty_class
        ):
            raise ClientError("active window is not the configured Ghostty/remote-Herdr client")
        return identity

    def require_same_active(self, expected: WindowIdentity) -> None:
        if self.active_identity() != expected:
            raise ClientError("active Ghostty window changed during target binding")

    def inject_binder(self, expected: WindowIdentity) -> None:
        inject_binder_chord(self.config, expected, self.require_same_active)

    def next_event(self, timeout: float = POLL_SECONDS):
        if self.display.pending_events():
            return self.display.next_event()
        ready, _, _ = select.select([self.display.fileno()], [], [], timeout)
        return self.display.next_event() if ready else None


def inject_binder_chord(
    config: ClientConfig,
    expected: WindowIdentity,
    require_same_active: Callable[[WindowIdentity], None],
) -> None:
    require_same_active(expected)
    try:
        result = subprocess.run(
            [
                config.xdotool_path,
                "key",
                "--window",
                str(expected.window_id),
                "--clearmodifiers",
                config.herdr_prefix,
                config.binder_key,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ClientError(f"could not inject the Herdr binder chord: {error}") from error
    if result.returncode != 0:
        raise ClientError("xdotool refused exact-window binder chord injection")
    require_same_active(expected)


class LaptopApplication:
    """Production state machine, with X11/process adapters injectable for tests."""

    def __init__(
        self,
        config: ClientConfig,
        x11,
        notifier: StatusNotifier | None = None,
        transport_factory: Callable[[ClientConfig], ProtocolTransport] = ProtocolTransport,
        capture_factory: Callable[
            [ClientConfig, Callable[[list[int]], None], Callable[[str], None]],
            MicrophoneCapture,
        ] = MicrophoneCapture,
    ):
        self.config = config
        self.x11 = x11
        self.notifier = notifier or StatusNotifier(config.notify_send_path)
        self.transport_factory = transport_factory
        self.capture_factory = capture_factory
        self.state = ClientState.OFF
        self.transport: ProtocolTransport | None = None
        self.capture: MicrophoneCapture | None = None
        self.request_id: str | None = None
        self._failure_lock = threading.Lock()
        self._async_failure: str | None = None
        self._set_state(ClientState.OFF, "Right-Control arms remote dictation")

    def _set_state(self, state: ClientState, detail: str = "") -> None:
        self.state = state
        self.notifier.show(state, detail)

    def arm(self) -> None:
        if self.state not in {ClientState.OFF, ClientState.FAILED}:
            return
        self.close_resources(send_cancel=True)
        try:
            self.x11.grab_dynamic()
            transport = self.transport_factory(self.config)
            transport.start()
            self.transport = transport
            self._set_state(ClientState.ARMED, "Space starts; Delete cancels")
        except ClientError as error:
            self.fail(str(error))

    def right_control(self) -> None:
        if self.state in {ClientState.OFF, ClientState.FAILED}:
            self.arm()
        else:
            self.close_resources(send_cancel=True)
            self._set_state(ClientState.OFF, "Remote dictation mode off")

    def space(self) -> None:
        if self.state == ClientState.ARMED:
            self.start_recording()
        elif self.state == ClientState.RECORDING:
            self.finish_recording()

    def delete(self) -> None:
        if self.state not in {ClientState.RECORDING, ClientState.PROCESSING}:
            return
        try:
            self._stop_capture()
            status = self._cancel_request()
            if status == "cancelled":
                self.request_id = None
                self._set_state(ClientState.ARMED, "Remote request cancelled")
            elif status == "cancelling":
                self._set_state(
                    ClientState.PROCESSING,
                    "Cancellation in progress on the workstation",
                )
            else:
                raise ClientError("cancel did not name a terminal or in-progress state")
        except ClientError as error:
            self.fail(str(error))

    def start_recording(self) -> None:
        transport = self.transport
        if transport is None:
            self.fail("SSH helper is unavailable")
            return
        try:
            intended = self.x11.active_identity()
            response = validate_response(
                transport.exchange(
                    {
                        "type": "start",
                        "version": PROTOCOL_VERSION,
                        "audio": {
                            "format": "s16le",
                            "sample_rate": 16000,
                            "channels": 1,
                        },
                    }
                ),
                {"pending"},
            )
            self.request_id = str(response["request_id"])
            self.x11.require_same_active(intended)
            self.x11.inject_binder(intended)
            deadline = time.monotonic() + BIND_TIMEOUT_SECONDS
            while True:
                if time.monotonic() >= deadline:
                    raise ClientError("exact Herdr pane binding timed out")
                status = self._status()
                if status["status"] == "bound":
                    break
                if status["status"] != "pending":
                    raise ClientError("workstation left pending state before exact binding")
                time.sleep(POLL_SECONDS)
            capture = self.capture_factory(
                self.config, self._send_audio, self._record_async_failure
            )
            capture.start()
            self.capture = capture
            self._set_state(ClientState.RECORDING, "Space finishes; Delete cancels")
        except ClientError as error:
            self.fail(str(error))

    def _send_audio(self, samples: list[int]) -> None:
        transport = self.transport
        request_id = self.request_id
        if transport is None or request_id is None:
            raise ClientError("audio has no live owning request")
        response = transport.exchange(
            {
                "type": "audio",
                "version": PROTOCOL_VERSION,
                "request_id": request_id,
                "pcm": samples,
            }
        )
        validate_response(response, {"accepted"}, request_id)

    def finish_recording(self) -> None:
        transport = self.transport
        request_id = self.request_id
        try:
            if transport is None or request_id is None:
                raise ClientError("recording has no live owning request")
            self._stop_capture()
            response = transport.exchange(
                {
                    "type": "finish",
                    "version": PROTOCOL_VERSION,
                    "request_id": request_id,
                }
            )
            validate_response(response, {"processing"}, request_id)
            self._set_state(ClientState.PROCESSING, "Workstation is processing")
        except ClientError as error:
            self.fail(str(error))

    def _status(self) -> dict[str, object]:
        if self.transport is None or self.request_id is None:
            raise ClientError("status has no live owning request")
        response = self.transport.exchange(
            {
                "type": "status",
                "version": PROTOCOL_VERSION,
                "request_id": self.request_id,
            }
        )
        return validate_response(
            response,
            {
                "pending",
                "bound",
                "processing",
                "cancelling",
                "succeeded",
                "failed",
                "cancelled",
            },
            self.request_id,
        )

    def tick(self) -> None:
        with self._failure_lock:
            failure, self._async_failure = self._async_failure, None
        if failure is not None:
            self.fail(failure)
            return
        if self.transport is not None and self.transport.unexpected_exit():
            self.fail("SSH helper exited")
            return
        if self.state == ClientState.PROCESSING:
            try:
                status = self._status()["status"]
                if status == "succeeded":
                    self.request_id = None
                    self._set_state(ClientState.ARMED, "Delivered to the bound Herdr pane")
                elif status == "failed":
                    self.fail("workstation transcription or delivery failed")
                elif status == "cancelled":
                    self.request_id = None
                    self._set_state(ClientState.ARMED, "Remote request cancelled")
                elif status not in {"processing", "cancelling"}:
                    raise ClientError(f"unexpected processing state: {status}")
            except ClientError as error:
                self.fail(str(error))

    def _record_async_failure(self, detail: str) -> None:
        with self._failure_lock:
            if self._async_failure is None:
                self._async_failure = detail

    def _stop_capture(self) -> None:
        capture, self.capture = self.capture, None
        if capture is not None:
            capture.stop()

    def _cancel_request(self) -> str | None:
        if self.transport is None or self.request_id is None:
            return None
        response = self.transport.exchange(
            {
                "type": "cancel",
                "version": PROTOCOL_VERSION,
                "request_id": self.request_id,
            }
        )
        validated = validate_response(
            response, {"cancelled", "cancelling"}, self.request_id
        )
        return str(validated["status"])

    def close_resources(self, send_cancel: bool) -> None:
        try:
            self._stop_capture()
        except ClientError as error:
            print(f"handy-remote-client: {error}", file=sys.stderr)
        if send_cancel:
            try:
                self._cancel_request()
            except ClientError:
                pass
        self.request_id = None
        if self.transport is not None:
            self.transport.close()
            self.transport = None
        self.x11.release_dynamic()

    def fail(self, detail: str) -> None:
        self.close_resources(send_cancel=True)
        self._set_state(ClientState.FAILED, detail)

    def close(self) -> None:
        self.close_resources(send_cancel=True)
        self._set_state(ClientState.OFF, "Remote dictation client stopped")


def default_config_path() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "qq-dictation" / "remote-laptop.json"


def run(config: ClientConfig) -> int:
    config.validate_runtime_tools()
    x11 = X11Controller(config)
    x11.grab_mode_key()
    application = LaptopApplication(config, x11)
    control = DistinctPressTracker()
    space = DistinctPressTracker()
    delete = DistinctPressTracker()

    def stop(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while True:
            event = x11.next_event()
            application.tick()
            if event is None or event.type not in (X.KeyPress, X.KeyRelease):
                continue
            if event.detail == x11.mode_keycode:
                if event.type == X.KeyRelease:
                    control.on_release(event.time)
                elif control.on_press(event.time):
                    application.right_control()
                    space.reset()
                    delete.reset()
                continue
            if event.detail == x11.space_keycode:
                if event.type == X.KeyRelease:
                    space.on_release(event.time)
                elif space.on_press(event.time):
                    application.space()
            elif event.detail == x11.cancel_keycode:
                if event.type == X.KeyRelease:
                    delete.on_release(event.time)
                elif delete.on_press(event.time):
                    application.delete()
    except KeyboardInterrupt:
        return 0
    finally:
        application.close()
        x11.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="validate configuration without opening X11 or starting subprocesses",
    )
    arguments = parser.parse_args(argv)
    try:
        config = ClientConfig.load(arguments.config)
        if arguments.check_config:
            print("remote laptop config: ok")
            return 0
        return run(config)
    except ClientError as error:
        print(f"handy-remote-client: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
