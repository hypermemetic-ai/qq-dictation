#!/usr/bin/env python3
"""Safely install qq-dictation's exact-pane binder in Herdr 0.7.5 config."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

BEGIN_MARKER = "# >>> qq-dictation remote binder >>>"
END_MARKER = "# <<< qq-dictation remote binder <<<"
BINDER_CHORD = "prefix+alt+d"
EXPECTED_HERDR_VERSION = "herdr 0.7.5"


class PolicyError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConfigTarget:
    configured_path: Path
    target_path: Path
    metadata: os.stat_result
    symlink_inode: int | None
    symlink_value: str | None


def inspect_config_target(
    configured_path: Path, expected_uid: int | None = None
) -> ConfigTarget:
    expected_uid = os.geteuid() if expected_uid is None else expected_uid
    configured_path = configured_path.absolute()
    try:
        configured = configured_path.lstat()
    except OSError as error:
        raise PolicyError(f"Herdr config is unavailable: {error}") from error

    symlink_inode = None
    symlink_value = None
    if stat.S_ISLNK(configured.st_mode):
        if configured.st_uid != expected_uid:
            raise PolicyError("Herdr config symlink is not owned by the operator")
        symlink_inode = configured.st_ino
        symlink_value = os.readlink(configured_path)
        linked = Path(symlink_value)
        target_path = linked if linked.is_absolute() else configured_path.parent / linked
        target_path = Path(os.path.abspath(target_path))
        try:
            metadata = target_path.lstat()
        except OSError as error:
            raise PolicyError(f"Herdr config symlink target is unavailable: {error}") from error
        if stat.S_ISLNK(metadata.st_mode):
            raise PolicyError("Herdr config uses an ambiguous symlink chain")
    else:
        target_path = configured_path
        metadata = configured

    if not stat.S_ISREG(metadata.st_mode):
        raise PolicyError("Herdr config target must be one regular file")
    if metadata.st_uid != expected_uid:
        raise PolicyError("Herdr config target is not owned by the operator")
    if metadata.st_mode & stat.S_IWOTH:
        raise PolicyError("Herdr config target is world-writable")
    if metadata.st_nlink != 1:
        raise PolicyError("Herdr config target has ambiguous hard links")

    try:
        parent = target_path.parent.stat()
    except OSError as error:
        raise PolicyError(f"Herdr config target directory is unavailable: {error}") from error
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != expected_uid:
        raise PolicyError("Herdr config target directory is not operator-owned")
    if parent.st_mode & stat.S_IWOTH:
        raise PolicyError("Herdr config target directory is world-writable")

    return ConfigTarget(
        configured_path=configured_path,
        target_path=target_path,
        metadata=metadata,
        symlink_inode=symlink_inode,
        symlink_value=symlink_value,
    )


def validate_binder(path: Path, expected_uid: int) -> Path:
    path = path.absolute()
    if not path.is_absolute():
        raise PolicyError("binder path must be absolute")
    try:
        metadata = path.stat()
    except OSError as error:
        raise PolicyError(f"installed binder is unavailable: {error}") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or metadata.st_mode & stat.S_IWOTH
        or not os.access(path, os.X_OK)
    ):
        raise PolicyError("installed binder is not a safe operator executable")
    return path


def marked_span(text: str) -> tuple[int, int] | None:
    begins = text.count(BEGIN_MARKER)
    ends = text.count(END_MARKER)
    if begins == 0 and ends == 0:
        return None
    if begins != 1 or ends != 1:
        raise PolicyError("Herdr config has absent, unmatched, or duplicate binder markers")
    start = text.index(BEGIN_MARKER)
    end_marker = text.index(END_MARKER)
    if end_marker < start:
        raise PolicyError("Herdr binder markers are out of order")
    end = end_marker + len(END_MARKER)
    if end < len(text) and text[end] == "\n":
        end += 1
    if text[end:].strip():
        raise PolicyError("marked Herdr binder block must remain the final config block")
    return start, end


def binder_block(binder: Path) -> str:
    command = json.dumps(str(binder), ensure_ascii=False)
    return (
        f"{BEGIN_MARKER}\n"
        "[[keys.command]]\n"
        f'key = "{BINDER_CHORD}"\n'
        'type = "shell"\n'
        f"command = {command}\n"
        f"{END_MARKER}\n"
    )


def parse_toml(text: str, subject: str) -> dict:
    try:
        value = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, UnicodeError) as error:
        raise PolicyError(f"{subject} is not valid TOML: {error}") from error
    if not isinstance(value, dict):
        raise PolicyError(f"{subject} is not a TOML table")
    return value


def command_entries(document: dict) -> list[dict]:
    keys = document.get("keys", {})
    if not isinstance(keys, dict):
        raise PolicyError("Herdr [keys] value is not a table")
    commands = keys.get("command", [])
    if not isinstance(commands, list) or not all(
        isinstance(command, dict) for command in commands
    ):
        raise PolicyError("Herdr keys.command value is not an array of tables")
    return commands


def check_unmarked_conflicts(document: dict, binder: Path) -> None:
    keys = document.get("keys", {})
    if not isinstance(keys, dict):
        raise PolicyError("Herdr [keys] value is not a table")
    for name, value in keys.items():
        if name not in {"command", "prefix", "indexed"} and value == BINDER_CHORD:
            raise PolicyError(f"Herdr chord {BINDER_CHORD} is occupied by keys.{name}")

    for command in command_entries(document):
        if command.get("key") == BINDER_CHORD:
            raise PolicyError(f"Herdr chord {BINDER_CHORD} is occupied by another command")
        configured_command = command.get("command")
        if configured_command == str(binder) or (
            isinstance(configured_command, str)
            and Path(configured_command).name == "handy-remote-bind.py"
        ):
            raise PolicyError("the qq-dictation binder command is configured on another chord")


def build_candidate(original: bytes, binder: Path) -> bytes:
    try:
        text = original.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PolicyError("Herdr config is not UTF-8") from error
    span = marked_span(text)
    block = binder_block(binder)

    if span is None:
        document = parse_toml(text, "Herdr config")
        check_unmarked_conflicts(document, binder)
        separator = "\n" if text.endswith("\n") else "\n\n"
        return (text + separator + block).encode("utf-8")

    start, end = span
    marked = text[start:end]
    marked_document = parse_toml(marked, "marked Herdr binder block")
    marked_commands = command_entries(marked_document)
    if len(marked_commands) != 1:
        raise PolicyError("marked Herdr binder block must contain exactly one command")
    marked_command = marked_commands[0]
    if (
        marked_command.get("key") != BINDER_CHORD
        or marked_command.get("type") != "shell"
        or set(marked_command) != {"key", "type", "command"}
        or not isinstance(marked_command.get("command"), str)
        or Path(marked_command["command"]).name != "handy-remote-bind.py"
    ):
        raise PolicyError("marked Herdr binder block conflicts with its owned policy")

    unmarked = text[:start]
    check_unmarked_conflicts(parse_toml(unmarked, "unmarked Herdr config"), binder)
    return (unmarked + block).encode("utf-8")


def run_command(
    argv: list[str], *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
            shell=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PolicyError(f"could not run {' '.join(argv)}: {error}") from error


def require_herdr_version(herdr: Path) -> None:
    result = run_command([str(herdr), "--version"])
    if result.returncode != 0 or result.stdout.strip() != EXPECTED_HERDR_VERSION:
        raise PolicyError(
            f"expected {EXPECTED_HERDR_VERSION}, got {result.stdout.strip() or result.stderr.strip()}"
        )


def validate_candidate(herdr: Path, candidate: Path) -> None:
    with tempfile.TemporaryDirectory(
        prefix=".qq-dictation-herdr-check.", dir=candidate.parent
    ) as isolated_xdg:
        environment = os.environ.copy()
        environment["HERDR_CONFIG_PATH"] = str(candidate)
        environment["XDG_CONFIG_HOME"] = isolated_xdg
        result = run_command([str(herdr), "config", "check"], env=environment)
    if result.returncode != 0 or "config: ok" not in result.stdout:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic"
        raise PolicyError(f"Herdr rejected the candidate config: {detail}")


def reload_and_check(herdr: Path) -> None:
    reload_result = run_command([str(herdr), "server", "reload-config"])
    if reload_result.returncode != 0:
        detail = reload_result.stderr.strip() or reload_result.stdout.strip()
        raise PolicyError(f"Herdr config reload failed: {detail or 'no diagnostic'}")
    status = run_command([str(herdr), "status", "server", "--json"])
    if status.returncode != 0:
        raise PolicyError("Herdr running-config status check failed")
    try:
        value = json.loads(status.stdout)
    except json.JSONDecodeError as error:
        raise PolicyError("Herdr running-config status was malformed") from error
    expected = {
        "status": "running",
        "running": True,
        "version": "0.7.5",
        "compatible": True,
        "restart_needed": False,
    }
    if not isinstance(value, dict) or any(value.get(key) != item for key, item in expected.items()):
        raise PolicyError("Herdr running config is not healthy after reload")
    if any("warning" in key.lower() and item for key, item in value.items()):
        raise PolicyError("Herdr running config reports a warning")


def materialize(path: Path, data: bytes, metadata: os.stat_result) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.qq-dictation.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), stat.S_IMODE(metadata.st_mode))
            os.fchown(stream.fileno(), metadata.st_uid, metadata.st_gid)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def candidate_file(target: ConfigTarget, data: bytes) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{target.target_path.name}.candidate.", dir=target.target_path.parent
    )
    candidate = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), stat.S_IMODE(target.metadata.st_mode))
            os.fchown(stream.fileno(), target.metadata.st_uid, target.metadata.st_gid)
        return candidate
    except Exception:
        candidate.unlink(missing_ok=True)
        raise


def backup_path(target: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return target.with_name(f"{target.name}.before-qq-dictation.{stamp}.{os.getpid()}")


def verify_source_unchanged(target: ConfigTarget, original: bytes) -> None:
    current = target.target_path.lstat()
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_dev != target.metadata.st_dev
        or current.st_ino != target.metadata.st_ino
        or current.st_uid != target.metadata.st_uid
        or current.st_gid != target.metadata.st_gid
        or stat.S_IMODE(current.st_mode) != stat.S_IMODE(target.metadata.st_mode)
        or target.target_path.read_bytes() != original
    ):
        raise PolicyError("Herdr config target changed during candidate validation")
    if target.symlink_inode is not None:
        link = target.configured_path.lstat()
        if (
            not stat.S_ISLNK(link.st_mode)
            or link.st_ino != target.symlink_inode
            or os.readlink(target.configured_path) != target.symlink_value
        ):
            raise PolicyError("Herdr config symlink changed during candidate validation")


def verify_identity(target: ConfigTarget) -> None:
    current = target.target_path.stat()
    if (
        current.st_uid != target.metadata.st_uid
        or current.st_gid != target.metadata.st_gid
        or stat.S_IMODE(current.st_mode) != stat.S_IMODE(target.metadata.st_mode)
    ):
        raise PolicyError("Herdr config target owner or mode changed")
    if target.symlink_inode is not None:
        link = target.configured_path.lstat()
        if (
            not stat.S_ISLNK(link.st_mode)
            or link.st_ino != target.symlink_inode
            or os.readlink(target.configured_path) != target.symlink_value
        ):
            raise PolicyError("Herdr config symlink identity changed")


def configure(configured_path: Path, binder_path: Path, herdr: Path) -> Path | None:
    target = inspect_config_target(configured_path)
    binder = validate_binder(binder_path, target.metadata.st_uid)
    original = target.target_path.read_bytes()
    candidate_bytes = build_candidate(original, binder)
    if candidate_bytes == original:
        verify_identity(target)
        return None

    require_herdr_version(herdr)
    candidate = candidate_file(target, candidate_bytes)
    backup = backup_path(target.target_path)
    replaced = False
    try:
        validate_candidate(herdr, candidate)
        verify_source_unchanged(target, original)
        materialize(backup, original, target.metadata)
        verify_source_unchanged(target, original)
        os.replace(candidate, target.target_path)
        replaced = True
        verify_identity(target)
        try:
            reload_and_check(herdr)
        except PolicyError as install_error:
            materialize(target.target_path, original, target.metadata)
            verify_identity(target)
            rollback_error = None
            try:
                reload_and_check(herdr)
            except PolicyError as error:
                rollback_error = error
            if rollback_error is not None:
                raise PolicyError(
                    f"{install_error}; target rolled back but running-config rollback failed: {rollback_error}"
                ) from install_error
            raise PolicyError(f"{install_error}; target and running config rolled back") from install_error
        return backup
    finally:
        if not replaced:
            candidate.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--binder", required=True, type=Path)
    parser.add_argument("--herdr", default=Path("/home/linuxbrew/.linuxbrew/bin/herdr"), type=Path)
    arguments = parser.parse_args(argv)
    try:
        backup = configure(arguments.config, arguments.binder, arguments.herdr)
        if backup is None:
            print("Herdr remote binder already configured")
        else:
            print(f"Herdr remote binder configured; backup retained at {backup}")
        return 0
    except (OSError, PolicyError, shutil.Error) as error:
        print(f"configure-remote-herdr: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
