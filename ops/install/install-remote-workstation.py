#!/usr/bin/env python3
"""Install the workstation SSH stream helper without touching Herdr config."""

from __future__ import annotations

import argparse
import os
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


class InstallError(RuntimeError):
    pass


def backup_name(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return path.with_name(f"{path.name}.before-qq-dictation.{stamp}.{os.getpid()}")


def atomic_bytes(path: Path, data: bytes, mode: int) -> None:
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.install.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def install_file(source: Path, destination: Path, mode: int) -> Path | None:
    data = source.read_bytes()
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    if destination.exists() or destination.is_symlink():
        metadata = destination.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
            raise InstallError(f"refusing unsafe existing install path {destination}")
        if destination.read_bytes() == data and stat.S_IMODE(metadata.st_mode) == mode:
            return None
        backup = backup_name(destination)
        atomic_bytes(backup, destination.read_bytes(), stat.S_IMODE(metadata.st_mode))
    else:
        backup = None
    atomic_bytes(destination, data, mode)
    return backup


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path.home())
    arguments = parser.parse_args(argv)
    home = arguments.home.absolute()
    local_bin = home / ".local" / "bin"
    try:
        home_metadata = home.stat()
        if not stat.S_ISDIR(home_metadata.st_mode) or home_metadata.st_uid != os.geteuid():
            raise InstallError("installation home must be an operator-owned directory")
        destination = local_bin / "handy-remote-stream.py"
        install_file(root / "ops" / "install" / destination.name, destination, 0o755)
        print(f"Installed remote workstation stream helper at {destination}")
        return 0
    except (InstallError, OSError) as error:
        print(f"install-remote-workstation: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
