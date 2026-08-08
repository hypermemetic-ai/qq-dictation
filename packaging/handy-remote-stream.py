#!/usr/bin/env python3
"""SSH-invoked byte bridge to the running qq-dictation app.

This helper deliberately understands no transcription or targeting policy. It
copies one framed protocol stream between stdin/stdout and the owner-only Unix
socket created by the app. SSH remains the only remote authentication boundary.
"""

from __future__ import annotations

import os
import select
import socket
import stat
import sys
from pathlib import Path

SOCKET_RELATIVE_PATH = Path("qq-dictation") / "remote.sock"
COPY_CHUNK_BYTES = 64 * 1024


def socket_path() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime_dir:
        raise RuntimeError("XDG_RUNTIME_DIR is required")
    path = Path(runtime_dir)
    if not path.is_absolute():
        raise RuntimeError("XDG_RUNTIME_DIR must be absolute")
    return path / SOCKET_RELATIVE_PATH


def validate_socket(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RuntimeError(f"remote dictation socket is unavailable: {exc}") from exc
    if not stat.S_ISSOCK(metadata.st_mode):
        raise RuntimeError("remote dictation path is not a Unix socket")
    if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
        raise RuntimeError("remote dictation socket is not owner-only")


def write_all(output_fd: int, data: bytes) -> None:
    """Write every byte, including when POSIX reports a legal short write."""
    remaining = memoryview(data)
    while remaining:
        written = os.write(output_fd, remaining)
        if written <= 0:
            raise OSError("stdout write made no progress")
        remaining = remaining[written:]


def bridge(stream: socket.socket, input_fd: int, output_fd: int) -> None:
    """Copy both directions until the app socket closes.

    A single select loop makes app/helper replacement observable even while SSH
    keeps stdin open; a thread blocked forever in ``read(stdin)`` would prevent
    truthful helper termination.
    """
    input_open = True
    while True:
        readers = [stream]
        if input_open:
            readers.append(input_fd)
        ready, _, _ = select.select(readers, [], [])
        if stream in ready:
            chunk = stream.recv(COPY_CHUNK_BYTES)
            if not chunk:
                return
            write_all(output_fd, chunk)
        if input_open and input_fd in ready:
            chunk = os.read(input_fd, COPY_CHUNK_BYTES)
            if chunk:
                stream.sendall(chunk)
            else:
                input_open = False
                stream.shutdown(socket.SHUT_WR)


def main() -> int:
    try:
        path = socket_path()
        validate_socket(path)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
            stream.connect(str(path))
            bridge(stream, sys.stdin.fileno(), sys.stdout.fileno())
        return 0
    except (BrokenPipeError, OSError, RuntimeError) as exc:
        print(f"handy-remote-stream: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
