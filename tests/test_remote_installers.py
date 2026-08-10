"""Hermetic production-policy tests for remote per-user installers."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).parents[1]
WORKSTATION_INSTALLER = ROOT / "ops" / "install" / "install-remote-workstation.py"
LAPTOP_INSTALLER = ROOT / "ops" / "install" / "install-remote-laptop.py"

INSTALLER_SPEC = importlib.util.spec_from_file_location(
    "qq_dictation_install_remote_laptop", LAPTOP_INSTALLER
)
assert INSTALLER_SPEC is not None and INSTALLER_SPEC.loader is not None
INSTALLER_MODULE = importlib.util.module_from_spec(INSTALLER_SPEC)
sys.modules[INSTALLER_SPEC.name] = INSTALLER_MODULE
INSTALLER_SPEC.loader.exec_module(INSTALLER_MODULE)

HEALTHY_SERVICE_STATE = """\
ActiveState=active
SubState=running
MainPID=4242
NRestarts=0
"""


def executable(path: Path, content: str) -> Path:
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    path.chmod(0o755)
    return path


def laptop_command(home: Path, systemctl: Path) -> list[str]:
    return [
        "/usr/bin/python3",
        str(LAPTOP_INSTALLER),
        "--home",
        str(home),
        "--ssh-host",
        "workstation",
        "--ghostty-title",
        "remote herdr",
        "--ghostty-class",
        "com.mitchellh.ghostty",
        "--systemctl",
        str(systemctl),
    ]


def local_laptop_command(home: Path, systemctl: Path, xdotool: Path) -> list[str]:
    return [
        "/usr/bin/python3",
        str(LAPTOP_INSTALLER),
        "--home",
        str(home),
        "--ssh-host",
        "workstation",
        "--delivery-mode",
        "local",
        "--xdotool-path",
        str(xdotool),
        "--systemctl",
        str(systemctl),
    ]


def requested_arguments(**updates):
    values = {
        "ssh_host": "workstation",
        "delivery_mode": None,
        "ghostty_title": "remote herdr",
        "ghostty_class": "com.mitchellh.ghostty",
        "capture_argv_json": json.dumps(INSTALLER_MODULE.DEFAULT_CAPTURE_ARGV),
        "ssh_path": "/usr/bin/ssh",
        "remote_helper": "~/.local/bin/handy-remote-stream.py",
        "notify_send_path": "/usr/bin/notify-send",
        "xdotool_path": None,
    }
    values.update(updates)
    return SimpleNamespace(**values)


def fake_systemctl(
    path: Path,
    log: Path,
    show_outputs: list[str],
    status_diagnostic: str = "synthetic service diagnostic",
    failed_command: list[str] | None = None,
    command_failure_diagnostic: str = "synthetic command failure",
) -> Path:
    counter = path.with_suffix(".show-count")
    return executable(
        path,
        f"""
        #!/usr/bin/python3
        import pathlib
        import sys

        arguments = sys.argv[1:]
        with pathlib.Path({str(log)!r}).open("a", encoding="utf-8") as stream:
            stream.write(" ".join(arguments) + "\\n")
        if arguments[:2] == ["--user", "show"]:
            counter = pathlib.Path({str(counter)!r})
            count = int(counter.read_text()) if counter.exists() else 0
            outputs = {show_outputs!r}
            sys.stdout.write(outputs[min(count, len(outputs) - 1)])
            counter.write_text(str(count + 1))
            raise SystemExit(0)
        if arguments[:1] == ["status"]:
            print({status_diagnostic!r}, file=sys.stderr)
            raise SystemExit(3)
        if arguments == {failed_command!r}:
            print({command_failure_diagnostic!r}, file=sys.stderr)
            raise SystemExit(1)
        raise SystemExit(0)
        """,
    )


def completed(command, returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


class InstallerTests(unittest.TestCase):
    def test_workstation_installer_only_places_stream_helper_and_never_reads_herdr_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            home = directory / "home"
            home.mkdir()
            herdr_dir = home / ".config" / "herdr"
            herdr_dir.mkdir(parents=True)
            target = directory / "operator-herdr-config.toml"
            original = b"operator-owned = true\n"
            target.write_bytes(original)
            configured = herdr_dir / "config.toml"
            configured.symlink_to(target)
            link_inode = configured.lstat().st_ino
            link_value = os.readlink(configured)
            target.chmod(0)

            local_bin = home / ".local" / "bin"
            local_bin.mkdir(parents=True)
            stream = local_bin / "handy-remote-stream.py"
            stream.write_text("old", encoding="utf-8")
            command = [
                "/usr/bin/python3",
                str(WORKSTATION_INSTALLER),
                "--home",
                str(home),
            ]
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                stream.read_bytes(),
                (ROOT / "ops" / "install" / "handy-remote-stream.py").read_bytes(),
            )
            self.assertEqual(stat.S_IMODE(stream.stat().st_mode), 0o755)
            self.assertFalse((local_bin / "handy-remote-bind.py").exists())
            self.assertEqual(configured.lstat().st_ino, link_inode)
            self.assertEqual(os.readlink(configured), link_value)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0)
            target.chmod(0o600)
            self.assertEqual(target.read_bytes(), original)

            backup_count = len(list(local_bin.glob("*.before-qq-dictation.*")))
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                len(list(local_bin.glob("*.before-qq-dictation.*"))), backup_count
            )

            rejected = subprocess.run(
                command + ["--config", str(configured)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("unrecognized arguments", rejected.stderr)

    def test_requested_config_preserves_legacy_and_supports_explicit_per_install_modes(self):
        legacy = INSTALLER_MODULE.requested_config(requested_arguments())
        self.assertNotIn("delivery_mode", legacy)
        self.assertNotIn("xdotool_path", legacy)
        self.assertEqual(legacy["ghostty_title"], "remote herdr")

        explicit_herdr = INSTALLER_MODULE.requested_config(
            requested_arguments(delivery_mode="herdr")
        )
        self.assertEqual(explicit_herdr["delivery_mode"], "herdr")
        self.assertNotIn("xdotool_path", explicit_herdr)

        local = INSTALLER_MODULE.requested_config(
            requested_arguments(
                delivery_mode="local",
                ghostty_title=None,
                ghostty_class=None,
            )
        )
        self.assertEqual(
            local,
            {
                "ssh_host": "workstation",
                "capture_argv": INSTALLER_MODULE.DEFAULT_CAPTURE_ARGV,
                "ssh_path": "/usr/bin/ssh",
                "remote_helper": "~/.local/bin/handy-remote-stream.py",
                "notify_send_path": "/usr/bin/notify-send",
                "delivery_mode": "local",
                "xdotool_path": INSTALLER_MODULE.DEFAULT_XDOTOOL_PATH,
            },
        )

    def test_requested_config_refuses_cross_mode_target_fields(self):
        with self.assertRaisesRegex(INSTALLER_MODULE.InstallError, "Ghostty target"):
            INSTALLER_MODULE.requested_config(
                requested_arguments(delivery_mode="local")
            )
        with self.assertRaisesRegex(INSTALLER_MODULE.InstallError, "must not configure xdotool"):
            INSTALLER_MODULE.requested_config(
                requested_arguments(delivery_mode="herdr", xdotool_path="/tmp/xdotool")
            )

    def test_runtime_requires_xdotool_only_for_local_delivery(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            ssh = executable(directory / "ssh", "#!/bin/sh\nexit 0\n")
            notify = executable(directory / "notify-send", "#!/bin/sh\nexit 0\n")
            capture = executable(directory / "pw-record", "#!/bin/sh\nexit 0\n")
            missing_xdotool = directory / "missing-xdotool"
            config = directory / "remote-laptop.json"
            common = {
                "ssh_path": str(ssh),
                "notify_send_path": str(notify),
                "capture_argv": [str(capture)],
            }

            with mock.patch.object(
                INSTALLER_MODULE.subprocess,
                "run",
                return_value=completed(["python"], stdout=""),
            ):
                config.write_text(json.dumps(common), encoding="utf-8")
                INSTALLER_MODULE.validate_laptop_runtime(config)

                config.write_text(
                    json.dumps(
                        {
                            **common,
                            "delivery_mode": "local",
                            "xdotool_path": str(missing_xdotool),
                        }
                    ),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(
                    INSTALLER_MODULE.InstallError, "missing-xdotool"
                ):
                    INSTALLER_MODULE.validate_laptop_runtime(config)

                executable(missing_xdotool, "#!/bin/sh\nexit 0\n")
                INSTALLER_MODULE.validate_laptop_runtime(config)

    def test_local_install_is_private_idempotent_and_refuses_different_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            home = directory / "home"
            home.mkdir()
            systemctl_log = directory / "systemctl.log"
            systemctl = fake_systemctl(
                directory / "systemctl",
                systemctl_log,
                [HEALTHY_SERVICE_STATE],
            )
            xdotool = executable(directory / "xdotool", "#!/bin/sh\nexit 0\n")
            command = local_laptop_command(home, systemctl, xdotool)

            result = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            config = home / ".config" / "qq-dictation" / "remote-laptop.json"
            client = home / ".local" / "bin" / "handy-remote-client.py"
            service = home / ".config" / "systemd" / "user" / "handy-remote-client.service"
            value = json.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(value["delivery_mode"], "local")
            self.assertEqual(value["xdotool_path"], str(xdotool))
            self.assertNotIn("ghostty_title", value)
            self.assertNotIn("ghostty_class", value)
            self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)

            backup_count = len(list(home.rglob("*.before-qq-dictation.*")))
            rerun = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(rerun.returncode, 0, rerun.stderr)
            self.assertEqual(
                len(list(home.rglob("*.before-qq-dictation.*"))), backup_count
            )

            before = {path: path.read_bytes() for path in (config, client, service)}
            command_count = len(systemctl_log.read_text(encoding="utf-8").splitlines())
            other_xdotool = executable(
                directory / "other-xdotool", "#!/bin/sh\nexit 0\n"
            )
            refused = subprocess.run(
                local_laptop_command(home, systemctl, other_xdotool),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("differs", refused.stderr)
            for path, data in before.items():
                self.assertEqual(path.read_bytes(), data)
            self.assertEqual(
                len(systemctl_log.read_text(encoding="utf-8").splitlines()),
                command_count,
            )

    def test_laptop_installer_health_gates_first_install_and_idempotent_rerun(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            home = directory / "home"
            home.mkdir()
            systemctl_log = directory / "systemctl.log"
            systemctl = fake_systemctl(
                directory / "systemctl",
                systemctl_log,
                [HEALTHY_SERVICE_STATE],
            )
            client_path = home / ".local" / "bin" / "handy-remote-client.py"
            client_path.parent.mkdir(parents=True)
            client_path.write_text("old client", encoding="utf-8")
            command = laptop_command(home, systemctl)
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Installed remote laptop client", result.stdout)

            config = home / ".config" / "qq-dictation" / "remote-laptop.json"
            service = home / ".config" / "systemd" / "user" / "handy-remote-client.service"
            self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(client_path.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE(service.stat().st_mode), 0o644)
            value = json.loads(config.read_text())
            self.assertEqual(value["ssh_host"], "workstation")
            self.assertEqual(
                value["capture_argv"],
                [
                    "/usr/bin/pw-record",
                    "--rate",
                    "16000",
                    "--channels",
                    "1",
                    "--format",
                    "s16",
                    "-",
                ],
            )
            for obsolete in (
                "delivery_mode",
                "herdr_prefix",
                "binder_key",
                "xdotool_path",
                "password",
            ):
                self.assertNotIn(obsolete, value)
            self.assertIn("ExecStart=/usr/bin/python3", service.read_text())
            first_commands = systemctl_log.read_text().splitlines()
            self.assertEqual(
                first_commands[0:3],
                [
                    "--user daemon-reload",
                    "--user enable handy-remote-client.service",
                    "--user restart handy-remote-client.service",
                ],
            )
            self.assertNotIn("--now", " ".join(first_commands))
            self.assertEqual(
                len(
                    [
                        command
                        for command in first_commands
                        if command.startswith("--user show ")
                    ]
                ),
                2,
            )

            backup_count = len(list(home.rglob("*.before-qq-dictation.*")))
            rerun = [
                "/usr/bin/python3",
                str(LAPTOP_INSTALLER),
                "--home",
                str(home),
                "--systemctl",
                str(systemctl),
            ]
            result = subprocess.run(rerun, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Installed remote laptop client", result.stdout)
            self.assertEqual(
                len(list(home.rglob("*.before-qq-dictation.*"))), backup_count
            )
            all_commands = systemctl_log.read_text().splitlines()
            self.assertEqual(
                len(
                    [
                        command
                        for command in all_commands
                        if command.startswith("--user show ")
                    ]
                ),
                4,
            )
            self.assertEqual(
                all_commands[len(first_commands) : len(first_commands) + 3],
                [
                    "--user daemon-reload",
                    "--user enable handy-remote-client.service",
                    "--user restart handy-remote-client.service",
                ],
            )
            self.assertNotIn("--now", " ".join(all_commands))

    def test_laptop_installer_restart_failure_is_nonzero_with_exact_service_diagnostic(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            home = directory / "home"
            home.mkdir()
            systemctl_log = directory / "systemctl.log"
            systemctl = fake_systemctl(
                directory / "systemctl",
                systemctl_log,
                [HEALTHY_SERVICE_STATE],
                status_diagnostic="exact restart service diagnostic",
                failed_command=[
                    "--user",
                    "restart",
                    "handy-remote-client.service",
                ],
                command_failure_diagnostic="synthetic restart command failure",
            )

            result = subprocess.run(
                laptop_command(home, systemctl),
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("Installed remote laptop client", result.stdout)
            self.assertIn(
                "--user restart handy-remote-client.service failed: "
                "synthetic restart command failure",
                result.stderr,
            )
            self.assertIn("exact restart service diagnostic", result.stderr)
            commands = systemctl_log.read_text().splitlines()
            self.assertEqual(
                commands[:4],
                [
                    "--user daemon-reload",
                    "--user enable handy-remote-client.service",
                    "--user restart handy-remote-client.service",
                    "status --user --no-pager --lines=20 handy-remote-client.service",
                ],
            )
            self.assertFalse(
                any(command.startswith("--user show ") for command in commands)
            )
            self.assertNotIn("--now", " ".join(commands))

    def test_existing_laptop_config_must_be_mode_0600_before_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            home = directory / "home"
            home.mkdir()
            systemctl_log = directory / "systemctl.log"
            systemctl = fake_systemctl(
                directory / "systemctl",
                systemctl_log,
                [HEALTHY_SERVICE_STATE],
            )
            result = subprocess.run(
                laptop_command(home, systemctl), capture_output=True, text=True, check=False
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            config = home / ".config" / "qq-dictation" / "remote-laptop.json"
            client_path = home / ".local" / "bin" / "handy-remote-client.py"
            service = home / ".config" / "systemd" / "user" / "handy-remote-client.service"
            config.chmod(0o644)
            client_path.write_bytes(b"preserve existing client")
            service.write_bytes(b"preserve existing service")
            before = {path: path.read_bytes() for path in (config, client_path, service)}
            systemctl_log.unlink()

            rerun = [
                "/usr/bin/python3",
                str(LAPTOP_INSTALLER),
                "--home",
                str(home),
                "--systemctl",
                str(systemctl),
            ]
            result = subprocess.run(rerun, capture_output=True, text=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("mode 0600", result.stderr)
            for path, data in before.items():
                self.assertEqual(path.read_bytes(), data)
            self.assertFalse(systemctl_log.exists())

    def test_laptop_service_failure_is_nonzero_and_config_remains_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            home = directory / "home"
            home.mkdir()
            systemctl = executable(
                directory / "systemctl",
                "#!/bin/sh\necho synthetic systemctl failure >&2\nexit 1\n",
            )
            result = subprocess.run(
                laptop_command(home, systemctl), capture_output=True, text=True, check=False
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("synthetic systemctl failure", result.stderr)
            config = home / ".config" / "qq-dictation" / "remote-laptop.json"
            self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)

    def test_service_health_observes_stable_state_across_fixed_restart_window(self):
        calls = []
        sleeps = []

        def runner(command, **_kwargs):
            calls.append(command)
            return completed(command, stdout=HEALTHY_SERVICE_STATE)

        INSTALLER_MODULE.verify_service_health(
            Path("/fake/systemctl"), runner=runner, sleeper=sleeps.append
        )
        self.assertEqual(sleeps, [INSTALLER_MODULE.SERVICE_HEALTH_OBSERVATION_SECONDS])
        self.assertGreater(INSTALLER_MODULE.SERVICE_HEALTH_OBSERVATION_SECONDS, 1.0)
        self.assertEqual(len(calls), 2)

    def test_service_health_rejects_pid_or_restart_change_while_still_running(self):
        cases = {
            "MainPID": """\
ActiveState=active
SubState=running
MainPID=4243
NRestarts=0
""",
            "restart count": """\
ActiveState=active
SubState=running
MainPID=4242
NRestarts=1
""",
        }
        for expected, final_state in cases.items():
            with self.subTest(expected=expected):
                states = iter((HEALTHY_SERVICE_STATE, final_state))

                def runner(command, **_kwargs):
                    if "show" in command:
                        return completed(command, stdout=next(states))
                    return completed(
                        command,
                        returncode=3,
                        stderr="exact service status diagnostic",
                    )

                with self.assertRaises(INSTALLER_MODULE.InstallError) as raised:
                    INSTALLER_MODULE.verify_service_health(
                        Path("/fake/systemctl"),
                        runner=runner,
                        sleeper=lambda _seconds: None,
                    )
                self.assertIn(expected, str(raised.exception))
                self.assertIn("exact service status diagnostic", str(raised.exception))

    def test_service_health_rejects_malformed_missing_and_duplicate_properties(self):
        cases = {
            "malformed": """\
ActiveState=active
SubState=running
MainPID=not-a-pid
NRestarts=0
""",
            "missing": """\
ActiveState=active
SubState=running
MainPID=4242
""",
            "duplicate": """\
ActiveState=active
SubState=running
MainPID=4242
NRestarts=0
NRestarts=0
""",
        }
        for name, state in cases.items():
            with self.subTest(name=name):
                sleeps = []

                def runner(command, **_kwargs):
                    if "show" in command:
                        return completed(command, stdout=state)
                    return completed(
                        command,
                        returncode=3,
                        stderr=f"{name} exact service diagnostic",
                    )

                with self.assertRaises(INSTALLER_MODULE.InstallError) as raised:
                    INSTALLER_MODULE.verify_service_health(
                        Path("/fake/systemctl"), runner=runner, sleeper=sleeps.append
                    )
                self.assertEqual(sleeps, [])
                self.assertIn(f"{name} exact service diagnostic", str(raised.exception))

    def test_service_failure_diagnostic_is_from_exact_service_and_bounded(self):
        calls = []

        def runner(command, **_kwargs):
            calls.append(command)
            if "show" in command:
                return completed(command, returncode=1, stderr="initiating show failure")
            return completed(
                command,
                returncode=3,
                stdout="exact service diagnostic\n"
                + "x" * (INSTALLER_MODULE.SERVICE_DIAGNOSTIC_LIMIT * 2),
            )

        with self.assertRaises(INSTALLER_MODULE.InstallError) as raised:
            INSTALLER_MODULE.verify_service_health(
                Path("/fake/systemctl"), runner=runner, sleeper=lambda _seconds: None
            )
        message = str(raised.exception)
        self.assertIn("initiating show failure", message)
        self.assertIn("exact service diagnostic", message)
        self.assertIn("[service diagnostic truncated]", message)
        self.assertLess(
            len(message), INSTALLER_MODULE.SERVICE_DIAGNOSTIC_LIMIT + 500
        )
        self.assertEqual(
            calls[-1][1:],
            [
                "status",
                "--user",
                "--no-pager",
                "--lines=20",
                "handy-remote-client.service",
            ],
        )

    def test_laptop_installer_rejects_active_then_failed_service_without_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            home = directory / "home"
            home.mkdir()
            systemctl_log = directory / "systemctl.log"
            failed_state = """\
ActiveState=failed
SubState=failed
MainPID=0
NRestarts=1
"""
            systemctl = fake_systemctl(
                directory / "systemctl",
                systemctl_log,
                [HEALTHY_SERVICE_STATE, failed_state],
                status_diagnostic=(
                    "handy-remote-client: Right-Control is already grabbed by another application"
                ),
            )
            result = subprocess.run(
                laptop_command(home, systemctl),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("Installed remote laptop client", result.stdout)
            self.assertIn("final service state is not healthy", result.stderr)
            self.assertIn(
                "handy-remote-client: Right-Control is already grabbed by another application",
                result.stderr,
            )
            commands = systemctl_log.read_text().splitlines()
            self.assertEqual(
                len([command for command in commands if command.startswith("--user show ")]),
                2,
            )
            self.assertIn(
                "status --user --no-pager --lines=20 handy-remote-client.service",
                commands,
            )

    def test_laptop_installer_does_not_overwrite_different_existing_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            home = directory / "home"
            home.mkdir()
            config = home / ".config" / "qq-dictation" / "remote-laptop.json"
            config.parent.mkdir(parents=True)
            config.write_text("{}\n", encoding="utf-8")
            config.chmod(0o600)
            before = config.read_bytes()
            systemctl = executable(directory / "systemctl", "#!/bin/sh\nexit 1\n")
            result = subprocess.run(
                laptop_command(home, systemctl), capture_output=True, text=True, check=False
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("differs", result.stderr)
            self.assertEqual(config.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
