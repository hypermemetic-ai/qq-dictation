#!/usr/bin/env python3
"""Reproducibly install the Linux/X11 remote qq-dictation laptop client."""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

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

SERVICE_NAME = "handy-remote-client.service"
SERVICE_HEALTH_PROPERTIES = ("ActiveState", "SubState", "MainPID", "NRestarts")
# This must remain longer than packaging/handy-remote-client.service's RestartSec=1.
SERVICE_HEALTH_OBSERVATION_SECONDS = 1.1
SERVICE_STATUS_LINES = 20
SERVICE_HEALTH_OUTPUT_LIMIT = 1_024
SERVICE_DIAGNOSTIC_LIMIT = 8_192


class InstallError(RuntimeError):
    pass


def backup_name(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return path.with_name(f"{path.name}.before-qq-dictation.{stamp}.{os.getpid()}")


def atomic_bytes(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700 if mode == 0o600 else 0o755)
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
    destination.parent.mkdir(parents=True, exist_ok=True)
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


def requested_config(arguments) -> dict | None:
    identity = (arguments.ssh_host, arguments.ghostty_title, arguments.ghostty_class)
    if not any(value is not None for value in identity):
        return None
    if not all(value is not None for value in identity):
        raise InstallError(
            "--ssh-host, --ghostty-title, and --ghostty-class must be supplied together"
        )
    try:
        capture = json.loads(arguments.capture_argv_json)
    except json.JSONDecodeError as error:
        raise InstallError(f"--capture-argv-json is invalid: {error}") from error
    return {
        "ssh_host": arguments.ssh_host,
        "ghostty_title": arguments.ghostty_title,
        "ghostty_class": arguments.ghostty_class,
        "capture_argv": capture,
        "ssh_path": arguments.ssh_path,
        "remote_helper": arguments.remote_helper,
        "notify_send_path": arguments.notify_send_path,
    }


def validate_config(client: Path, config: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(client), "--check-config", "--config", str(config)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )
    if result.returncode != 0:
        raise InstallError(result.stderr.strip() or "laptop configuration validation failed")


def validate_laptop_runtime(config: Path) -> None:
    dependency = subprocess.run(
        ["/usr/bin/python3", "-c", "import Xlib"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )
    if dependency.returncode != 0:
        raise InstallError("/usr/bin/python3 cannot import the required python-xlib module")
    value = json.loads(config.read_text(encoding="utf-8"))
    executables = [
        value["ssh_path"],
        value["notify_send_path"],
        value["capture_argv"][0],
    ]
    for executable in executables:
        path = Path(executable)
        if not path.is_file() or not os.access(path, os.X_OK):
            raise InstallError(f"required laptop executable is unavailable: {path}")


def bounded_diagnostic(value: str) -> str:
    value = value.strip()
    marker = "\n[service diagnostic truncated]"
    if len(value) <= SERVICE_DIAGNOSTIC_LIMIT:
        return value
    return value[: SERVICE_DIAGNOSTIC_LIMIT - len(marker)] + marker


def service_failure(systemctl: Path, reason: str, runner=subprocess.run) -> InstallError:
    command = [
        str(systemctl),
        "status",
        "--user",
        "--no-pager",
        f"--lines={SERVICE_STATUS_LINES}",
        SERVICE_NAME,
    ]
    try:
        result = runner(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            shell=False,
        )
        output = "\n".join(
            part.strip() for part in (result.stdout, result.stderr) if part.strip()
        )
        if output:
            diagnostic = bounded_diagnostic(output)
        else:
            diagnostic = (
                f"systemctl status exited with {result.returncode} without a diagnostic"
            )
    except (OSError, subprocess.SubprocessError) as error:
        diagnostic = bounded_diagnostic(f"systemctl status could not run: {error}")
    return InstallError(f"{reason}\n{SERVICE_NAME} diagnostic:\n{diagnostic}")


def parse_service_state(output: str) -> tuple[str, str, int, int]:
    if len(output) > SERVICE_HEALTH_OUTPUT_LIMIT:
        raise InstallError("systemctl show output exceeded the service health bound")
    lines = output.splitlines()
    if len(lines) != len(SERVICE_HEALTH_PROPERTIES):
        raise InstallError(
            "systemctl show returned a missing, duplicate, or extra service property"
        )
    properties: dict[str, str] = {}
    for line in lines:
        if line.count("=") != 1:
            raise InstallError("systemctl show returned a malformed service property")
        name, value = line.split("=", 1)
        if name not in SERVICE_HEALTH_PROPERTIES:
            raise InstallError(f"systemctl show returned unexpected property {name!r}")
        if name in properties:
            raise InstallError(f"systemctl show returned duplicate property {name}")
        if not value:
            raise InstallError(f"systemctl show returned an empty {name}")
        properties[name] = value
    if set(properties) != set(SERVICE_HEALTH_PROPERTIES):
        raise InstallError("systemctl show omitted a required service property")

    for name in ("MainPID", "NRestarts"):
        value = properties[name]
        if not value.isascii() or not value.isdigit():
            raise InstallError(f"systemctl show returned a malformed {name}")
    return (
        properties["ActiveState"],
        properties["SubState"],
        int(properties["MainPID"]),
        int(properties["NRestarts"]),
    )


def observe_service_health(
    systemctl: Path, observation: str, runner=subprocess.run
) -> tuple[str, str, int, int]:
    command = [
        str(systemctl),
        "--user",
        "show",
        SERVICE_NAME,
        *(f"--property={name}" for name in SERVICE_HEALTH_PROPERTIES),
        "--no-pager",
    ]
    try:
        result = runner(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise service_failure(
            systemctl, f"{observation} service health query could not run: {error}", runner
        ) from error
    if result.returncode != 0:
        detail = bounded_diagnostic(result.stderr or result.stdout)
        if not detail:
            detail = f"exit status {result.returncode} without a diagnostic"
        raise service_failure(
            systemctl, f"{observation} service health query failed: {detail}", runner
        )
    if result.stderr:
        raise service_failure(
            systemctl,
            (
                f"{observation} service health query returned an unexpected diagnostic: "
                f"{bounded_diagnostic(result.stderr)}"
            ),
            runner,
        )
    try:
        state = parse_service_state(result.stdout)
    except InstallError as error:
        raise service_failure(systemctl, f"{observation}: {error}", runner) from error
    active, substate, main_pid, _restarts = state
    if active != "active" or substate != "running" or main_pid <= 0:
        raise service_failure(
            systemctl,
            (
                f"{observation} service state is not healthy: "
                f"ActiveState={active}, SubState={substate}, MainPID={main_pid}"
            ),
            runner,
        )
    return state


def verify_service_health(
    systemctl: Path, runner=subprocess.run, sleeper=time.sleep
) -> None:
    initial = observe_service_health(systemctl, "initial", runner)
    sleeper(SERVICE_HEALTH_OBSERVATION_SECONDS)
    final = observe_service_health(systemctl, "final", runner)
    if final[2] != initial[2]:
        raise service_failure(
            systemctl,
            f"service MainPID changed during health observation: {initial[2]} -> {final[2]}",
            runner,
        )
    if final[3] != initial[3]:
        raise service_failure(
            systemctl,
            (
                "service restart count changed during health observation: "
                f"{initial[3]} -> {final[3]}"
            ),
            runner,
        )


def run_systemctl_command(
    systemctl: Path, arguments: list[str], runner=subprocess.run
) -> None:
    command = [str(systemctl), *arguments]
    try:
        result = runner(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise service_failure(
            systemctl, f"{' '.join(command)} could not run: {error}", runner
        ) from error
    if result.returncode != 0:
        detail = bounded_diagnostic(result.stderr or result.stdout)
        if not detail:
            detail = f"exit status {result.returncode} without a diagnostic"
        raise service_failure(
            systemctl, f"{' '.join(command)} failed: {detail}", runner
        )


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--ssh-host")
    parser.add_argument("--ghostty-title")
    parser.add_argument("--ghostty-class")
    parser.add_argument("--capture-argv-json", default=json.dumps(DEFAULT_CAPTURE_ARGV))
    parser.add_argument("--ssh-path", default="/usr/bin/ssh")
    parser.add_argument("--remote-helper", default="~/.local/bin/handy-remote-stream.py")
    parser.add_argument("--notify-send-path", default="/usr/bin/notify-send")
    parser.add_argument("--systemctl", type=Path, default=Path("/usr/bin/systemctl"))
    arguments = parser.parse_args(argv)

    home = arguments.home.absolute()
    client = home / ".local" / "bin" / "handy-remote-client.py"
    service = home / ".config" / "systemd" / "user" / "handy-remote-client.service"
    config = home / ".config" / "qq-dictation" / "remote-laptop.json"
    try:
        metadata = home.stat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid():
            raise InstallError("installation home must be an operator-owned directory")
        desired = requested_config(arguments)
        if config.exists() or config.is_symlink():
            config_metadata = config.lstat()
            if not stat.S_ISREG(config_metadata.st_mode) or config_metadata.st_uid != os.geteuid():
                raise InstallError("refusing unsafe existing laptop configuration")
            if stat.S_IMODE(config_metadata.st_mode) != 0o600:
                raise InstallError("existing laptop configuration must have mode 0600")
            if desired is not None:
                existing = json.loads(config.read_text(encoding="utf-8"))
                if existing != desired:
                    raise InstallError(
                        "existing laptop configuration differs; edit it explicitly instead of overwriting"
                    )
        else:
            if desired is None:
                raise InstallError(
                    "first install requires --ssh-host, --ghostty-title, and --ghostty-class"
                )
            config.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor, candidate_name = tempfile.mkstemp(
                prefix=".remote-laptop.candidate.", dir=config.parent
            )
            candidate = Path(candidate_name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(desired, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                    os.fchmod(stream.fileno(), 0o600)
                validate_config(root / "packaging" / "handy-remote-client.py", candidate)
                validate_laptop_runtime(candidate)
                os.replace(candidate, config)
            finally:
                candidate.unlink(missing_ok=True)

        # Validate before replacing an existing installed client.
        validate_config(root / "packaging" / "handy-remote-client.py", config)
        validate_laptop_runtime(config)
        install_file(root / "packaging" / "handy-remote-client.py", client, 0o755)
        install_file(root / "packaging" / "handy-remote-client.service", service, 0o644)
        validate_config(client, config)

        run_systemctl_command(
            arguments.systemctl, ["--user", "daemon-reload"]
        )
        run_systemctl_command(
            arguments.systemctl,
            ["--user", "enable", "--now", SERVICE_NAME],
        )
        verify_service_health(arguments.systemctl)
        print(f"Installed remote laptop client and mode-off service at {client}")
        return 0
    except (InstallError, OSError, json.JSONDecodeError, subprocess.SubprocessError) as error:
        print(f"install-remote-laptop: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
