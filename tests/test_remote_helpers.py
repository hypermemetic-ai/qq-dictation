"""Hermetic tests for the workstation SSH stream helper and Herdr binder."""

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
    "handy_remote_stream", "packaging/handy-remote-stream.py"
)
binder = load_script("handy_remote_bind", "packaging/handy-remote-bind.py")


class SocketPolicyTests(unittest.TestCase):
    def test_helpers_require_runtime_context(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "XDG_RUNTIME_DIR"):
                stream_helper.socket_path()
            with self.assertRaisesRegex(RuntimeError, "XDG_RUNTIME_DIR"):
                binder.context()

    def test_binder_requires_exact_herdr_context(self):
        with tempfile.TemporaryDirectory() as runtime_dir:
            with mock.patch.dict(
                os.environ, {"XDG_RUNTIME_DIR": runtime_dir}, clear=True
            ):
                with self.assertRaisesRegex(RuntimeError, "HERDR_ACTIVE_PANE_ID"):
                    binder.context()

    def test_helpers_refuse_regular_file_and_permissive_socket(self):
        with tempfile.TemporaryDirectory() as runtime_dir:
            path = Path(runtime_dir) / "remote.sock"
            path.write_text("not a socket", encoding="utf-8")
            for validate in (stream_helper.validate_socket, binder.validate_socket):
                with self.assertRaisesRegex(RuntimeError, "not a Unix socket"):
                    validate(path)

            path.unlink()
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(path))
            path.chmod(0o666)
            try:
                for validate in (
                    stream_helper.validate_socket,
                    binder.validate_socket,
                ):
                    with self.assertRaisesRegex(RuntimeError, "owner-only"):
                        validate(path)
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
                binder.validate_socket(path)
            finally:
                listener.close()


class StreamBridgeTests(unittest.TestCase):
    def test_app_socket_replacement_terminates_even_while_stdin_stays_open(self):
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


class BinderProtocolTests(unittest.TestCase):
    def test_binder_sends_versioned_exact_pane_claim(self):
        with tempfile.TemporaryDirectory() as runtime_dir:
            path = Path(runtime_dir) / "remote.sock"
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(path))
            listener.listen(1)
            path.chmod(0o600)
            observed: list[dict[str, object]] = []

            def serve():
                connection, _ = listener.accept()
                with connection:
                    length = int.from_bytes(connection.recv(4), "big")
                    payload = binder.read_exact(connection, length)
                    observed.append(json.loads(payload))
                    connection.sendall(
                        binder.encode_message(
                            {
                                "version": binder.PROTOCOL_VERSION,
                                "status": "bound",
                                "request_id": "synthetic-request",
                            }
                        )
                    )

            server = threading.Thread(target=serve)
            server.start()
            try:
                binder.bind(path, "w2H:p13")
            finally:
                server.join(timeout=2)
                listener.close()

            self.assertEqual(
                observed,
                [
                    {
                        "type": "bind",
                        "version": 1,
                        "pane_id": "w2H:p13",
                    }
                ],
            )

    def test_binder_refuses_failed_or_truncated_acknowledgement(self):
        left, right = socket.socketpair()
        try:
            right.sendall(
                binder.encode_message(
                    {"version": 1, "status": "error", "error": "no pending claim"}
                )
            )
            response = binder.read_response(left)
            self.assertEqual(response["status"], "error")
        finally:
            left.close()
            right.close()

        left, right = socket.socketpair()
        try:
            right.sendall((20).to_bytes(4, "big") + b"short")
            right.close()
            with self.assertRaisesRegex(RuntimeError, "truncated"):
                binder.read_response(left)
        finally:
            left.close()

    def test_binder_frame_bound_is_explicit(self):
        with self.assertRaisesRegex(RuntimeError, "frame bound"):
            binder.encode_message(
                {"pane_id": "x" * binder.MAX_PROTOCOL_FRAME_BYTES}
            )


if __name__ == "__main__":
    unittest.main()
