"""Hermetic production-policy tests for remote per-user installers."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
WORKSTATION_INSTALLER = ROOT / "scripts" / "install-remote-workstation.py"
LAPTOP_INSTALLER = ROOT / "scripts" / "install-remote-laptop.py"


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
                (ROOT / "packaging" / "handy-remote-stream.py").read_bytes(),
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

    def test_laptop_installer_writes_private_minimal_config_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            home = directory / "home"
            home.mkdir()
            systemctl_log = directory / "systemctl.log"
            systemctl = executable(
                directory / "systemctl",
                f"""
                #!/bin/sh
                printf '%s\\n' "$*" >> {str(systemctl_log)!r}
                exit 0
                """,
            )
            client_path = home / ".local" / "bin" / "handy-remote-client.py"
            client_path.parent.mkdir(parents=True)
            client_path.write_text("old client", encoding="utf-8")
            command = laptop_command(home, systemctl)
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)

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
            for obsolete in ("herdr_prefix", "binder_key", "xdotool_path", "password"):
                self.assertNotIn(obsolete, value)
            self.assertIn("ExecStart=/usr/bin/python3", service.read_text())
            self.assertEqual(
                systemctl_log.read_text().splitlines(),
                [
                    "--user daemon-reload",
                    "--user enable --now handy-remote-client.service",
                ],
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
            self.assertEqual(
                len(list(home.rglob("*.before-qq-dictation.*"))), backup_count
            )

    def test_existing_laptop_config_must_be_mode_0600_before_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            home = directory / "home"
            home.mkdir()
            systemctl_log = directory / "systemctl.log"
            systemctl = executable(
                directory / "systemctl",
                f"""
                #!/bin/sh
                printf '%s\\n' "$*" >> {str(systemctl_log)!r}
                exit 0
                """,
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
