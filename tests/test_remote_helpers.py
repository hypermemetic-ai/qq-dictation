"""Hermetic tests for the workstation SSH stream helper."""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parents[1]


def load_script(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stream_helper = load_script(
    "handy_remote_stream", "ops/install/handy-remote-stream.py"
)


def framed(message: dict[str, object]) -> bytes:
    payload = json.dumps(message, separators=(",", ":")).encode()
    return len(payload).to_bytes(4, "big") + payload


class SocketPolicyTests(unittest.TestCase):
    def test_helper_requires_absolute_runtime_context(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "XDG_RUNTIME_DIR"):
                stream_helper.socket_path()
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": "relative"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "absolute"):
                stream_helper.socket_path()

    def test_helper_refuses_regular_file_and_permissive_socket(self):
        with tempfile.TemporaryDirectory() as runtime_dir:
            path = Path(runtime_dir) / "remote.sock"
            path.write_text("not a socket", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "not a Unix socket"):
                stream_helper.validate_socket(path)

            path.unlink()
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(path))
            path.chmod(0o666)
            try:
                with self.assertRaisesRegex(RuntimeError, "owner-only"):
                    stream_helper.validate_socket(path)
            finally:
                listener.close()

    def test_owner_only_unix_socket_is_accepted(self):
        with tempfile.TemporaryDirectory() as runtime_dir:
            path = Path(runtime_dir) / "remote.sock"
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(path))
            path.chmod(0o600)
            try:
                stream_helper.validate_socket(path)
            finally:
                listener.close()


class StreamBridgeTests(unittest.TestCase):
    def test_complete_app_frame_survives_repeated_legal_short_stdout_writes(self):
        helper_socket, app_socket = socket.socketpair()
        input_read, input_write = os.pipe()
        output_read, output_write = os.pipe()
        response = framed(
            {"version": 1, "status": "ready", "request_id": "short-write-proof"}
        )
        real_write = os.write
        writes: list[int] = []

        def short_write(descriptor, data):
            limited = bytes(data[: max(1, len(data) // 3)])
            writes.append(len(limited))
            return real_write(descriptor, limited)

        with mock.patch.object(stream_helper.os, "write", side_effect=short_write):
            worker = threading.Thread(
                target=stream_helper.bridge,
                args=(helper_socket, input_read, output_write),
            )
            worker.start()
            app_socket.sendall(response)
            app_socket.close()
            worker.join(timeout=1)

        os.close(output_write)
        try:
            forwarded = bytearray()
            while chunk := os.read(output_read, 4096):
                forwarded.extend(chunk)
            self.assertFalse(worker.is_alive())
            self.assertGreater(len(writes), 1)
            self.assertLess(writes[0], len(response))
            self.assertEqual(bytes(forwarded), response)
        finally:
            helper_socket.close()
            for descriptor in (input_read, input_write, output_read):
                os.close(descriptor)

    def test_app_socket_replacement_terminates_while_stdin_stays_open(self):
        helper_socket, app_socket = socket.socketpair()
        input_read, input_write = os.pipe()
        output_read, output_write = os.pipe()
        worker = threading.Thread(
            target=stream_helper.bridge,
            args=(helper_socket, input_read, output_write),
        )
        worker.start()
        app_socket.close()
        worker.join(timeout=1)
        try:
            self.assertFalse(worker.is_alive())
        finally:
            helper_socket.close()
            for descriptor in (input_read, input_write, output_read, output_write):
                os.close(descriptor)


if __name__ == "__main__":
    unittest.main()
