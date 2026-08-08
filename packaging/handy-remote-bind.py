#!/usr/bin/env python3
"""Claim the sole pending remote dictation with Herdr's exact pane context.

Herdr 0.7.5 detached shell commands supply HERDR_ACTIVE_PANE_ID for the client
that invoked the command. This binder sends only that context to the app. The
installer-owned candidate transport chord is ``prefix+alt+d``; installation is
intentionally owned by the later laptop/configuration ticket and must keep the
chord configurable until its Ghostty/SSH path is proven live.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import sys
from pathlib import Path

PROTOCOL_VERSION = 1
MAX_PROTOCOL_FRAME_BYTES = 65_536
SOCKET_RELATIVE_PATH = Path("qq-dictation") / "remote.sock"


def context() -> tuple[Path, str]:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    pane_id = os.environ.get("HERDR_ACTIVE_PANE_ID")
    if not runtime_dir:
        raise RuntimeError("XDG_RUNTIME_DIR is required")
    if not pane_id:
        raise RuntimeError("HERDR_ACTIVE_PANE_ID is required")
    runtime_path = Path(runtime_dir)
    if not runtime_path.is_absolute():
        raise RuntimeError("XDG_RUNTIME_DIR must be absolute")
    return runtime_path / SOCKET_RELATIVE_PATH, pane_id


def validate_socket(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RuntimeError(f"remote dictation socket is unavailable: {exc}") from exc
    if not stat.S_ISSOCK(metadata.st_mode):
        raise RuntimeError("remote dictation path is not a Unix socket")
    if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
        raise RuntimeError("remote dictation socket is not owner-only")


def encode_message(message: dict[str, object]) -> bytes:
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    if not payload or len(payload) > MAX_PROTOCOL_FRAME_BYTES:
        raise RuntimeError("binder message is outside the protocol frame bound")
    return len(payload).to_bytes(4, "big") + payload


def read_exact(stream: socket.socket, length: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = stream.recv(length - len(chunks))
        if not chunk:
            raise RuntimeError("app closed a truncated binder response")
        chunks.extend(chunk)
    return bytes(chunks)


def read_response(stream: socket.socket) -> dict[str, object]:
    length = int.from_bytes(read_exact(stream, 4), "big")
    if length <= 0 or length > MAX_PROTOCOL_FRAME_BYTES:
        raise RuntimeError("app returned an invalid protocol frame length")
    try:
        response = json.loads(read_exact(stream, length))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("app returned a malformed binder response") from exc
    if not isinstance(response, dict):
        raise RuntimeError("app returned a non-object binder response")
    return response


def bind(path: Path, pane_id: str) -> None:
    validate_socket(path)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
        stream.connect(str(path))
        stream.sendall(
            encode_message(
                {
                    "type": "bind",
                    "version": PROTOCOL_VERSION,
                    "pane_id": pane_id,
                }
            )
        )
        response = read_response(stream)
    if (
        response.get("version") != PROTOCOL_VERSION
        or response.get("status") != "bound"
        or not isinstance(response.get("request_id"), str)
    ):
        detail = response.get("error", "claim was not acknowledged")
        raise RuntimeError(f"remote target bind refused: {detail}")


def main() -> int:
    try:
        path, pane_id = context()
        bind(path, pane_id)
        return 0
    except (OSError, RuntimeError) as exc:
        print(f"handy-remote-bind: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
