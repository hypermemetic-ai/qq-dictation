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
from unittest import mock

ROOT = Path(__file__).parents[1]
HERDR_SCRIPT = ROOT / "scripts" / "configure-remote-herdr.py"
WORKSTATION_INSTALLER = ROOT / "scripts" / "install-remote-workstation.py"
LAPTOP_INSTALLER = ROOT / "scripts" / "install-remote-laptop.py"
SPEC = importlib.util.spec_from_file_location("configure_remote_herdr", HERDR_SCRIPT)
assert SPEC is not None and SPEC.loader is not None
herdr_policy = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = herdr_policy
SPEC.loader.exec_module(herdr_policy)


def executable(path: Path, content: str) -> Path:
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    path.chmod(0o755)
    return path


def fake_herdr(directory: Path) -> tuple[Path, Path]:
    log = directory / "herdr.log"
    program = executable(
        directory / "herdr",
        f"""
        #!/usr/bin/python3
        import json, os, pathlib, sys, tomllib
        log = pathlib.Path({str(log)!r})
        args = sys.argv[1:]
        candidate = os.environ.get("HERDR_CONFIG_PATH")
        candidate_stat = pathlib.Path(candidate).stat() if candidate and pathlib.Path(candidate).exists() else None
        with log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({{"args": args, "config": candidate, "xdg": os.environ.get("XDG_CONFIG_HOME"), "candidate_mode": (candidate_stat.st_mode & 0o777) if candidate_stat else None, "candidate_uid": candidate_stat.st_uid if candidate_stat else None}}) + "\\n")
        if args == ["--version"]:
            print(os.environ.get("FAKE_HERDR_VERSION", "herdr 0.7.5"))
            raise SystemExit(0)
        if args == ["config", "check"]:
            path = pathlib.Path(os.environ["HERDR_CONFIG_PATH"])
            if os.environ.get("FAKE_HERDR_CHECK_FAIL"):
                print("synthetic parse failure", file=sys.stderr)
                raise SystemExit(1)
            tomllib.loads(path.read_text(encoding="utf-8"))
            print("config: ok")
            raise SystemExit(0)
        if args == ["server", "reload-config"]:
            counter = pathlib.Path({str(directory / 'reload.count')!r})
            count = int(counter.read_text() or "0") if counter.exists() else 0
            counter.write_text(str(count + 1))
            mode = os.environ.get("FAKE_HERDR_RELOAD_FAIL")
            if mode == "always" or (mode == "once" and count == 0):
                print("synthetic reload failure", file=sys.stderr)
                raise SystemExit(1)
            print("reloaded")
            raise SystemExit(0)
        if args == ["status", "server", "--json"]:
            counter = pathlib.Path({str(directory / 'status.count')!r})
            count = int(counter.read_text() or "0") if counter.exists() else 0
            counter.write_text(str(count + 1))
            if os.environ.get("FAKE_HERDR_STATUS_FAIL") == "once" and count == 0:
                print(json.dumps({{"status":"running","running":True,"version":"0.7.5","compatible":True,"restart_needed":True}}))
            else:
                print(json.dumps({{"status":"running","running":True,"version":"0.7.5","compatible":True,"restart_needed":False}}))
            raise SystemExit(0)
        raise SystemExit(2)
        """,
    )
    return program, log


def base_config() -> str:
    return (
        'onboarding = false\n\n'
        '[theme]\nname = "nord"\n\n'
        '[keys]\nprefix = "ctrl+b"\nnew_tab = "prefix+c"\n'
    )


class HerdrConfigPolicyTests(unittest.TestCase):
    def fixture(self, directory: Path, *, symlink=False):
        fake, log = fake_herdr(directory)
        binder = executable(directory / "handy-remote-bind.py", "#!/bin/sh\nexit 0\n")
        target = directory / "operator-config.toml"
        target.write_text(base_config(), encoding="utf-8")
        target.chmod(0o664)
        configured = directory / "config.toml"
        if symlink:
            configured.symlink_to(target.name)
        else:
            configured = target
        return configured, target, binder, fake, log

    def run_policy(self, config, binder, fake, **environment):
        env = os.environ.copy()
        env.update(environment)
        return subprocess.run(
            [
                "/usr/bin/python3",
                str(HERDR_SCRIPT),
                "--config",
                str(config),
                "--binder",
                str(binder),
                "--herdr",
                str(fake),
            ],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    def test_regular_target_insertion_backup_modes_and_exact_idempotency(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            configured, target, binder, fake, log = self.fixture(directory)
            original = target.read_bytes()
            result = self.run_policy(configured, binder, fake)
            self.assertEqual(result.returncode, 0, result.stderr)
            current = target.read_text(encoding="utf-8")
            self.assertTrue(current.startswith(original.decode("utf-8")))
            self.assertEqual(current.count(herdr_policy.BEGIN_MARKER), 1)
            self.assertIn('key = "prefix+alt+d"', current)
            self.assertIn(f"command = {json.dumps(str(binder))}", current)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o664)
            backups = list(directory.glob("operator-config.toml.before-qq-dictation.*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), original)
            self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o664)

            calls = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(
                [call["args"] for call in calls],
                [
                    ["--version"],
                    ["config", "check"],
                    ["server", "reload-config"],
                    ["status", "server", "--json"],
                ],
            )
            candidate = Path(calls[1]["config"])
            self.assertEqual(candidate.parent, target.parent)
            self.assertNotEqual(candidate, target)
            self.assertEqual(calls[1]["candidate_mode"], 0o664)
            self.assertEqual(calls[1]["candidate_uid"], os.geteuid())
            isolated_xdg = Path(calls[1]["xdg"])
            self.assertEqual(isolated_xdg.parent, target.parent)
            self.assertTrue(isolated_xdg.name.startswith(".qq-dictation-herdr-check."))
            self.assertFalse(isolated_xdg.exists())

            installed = target.read_bytes()
            result = self.run_policy(configured, binder, fake)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("already configured", result.stdout)
            self.assertEqual(target.read_bytes(), installed)
            self.assertEqual(len(list(directory.glob("*.before-qq-dictation.*"))), 1)
            self.assertEqual(len(log.read_text().splitlines()), 4)

    def test_symlink_target_preserves_link_identity_target_mode_and_target_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            configured, target, binder, fake, _log = self.fixture(directory, symlink=True)
            inode = configured.lstat().st_ino
            link_value = os.readlink(configured)
            original = target.read_bytes()
            result = self.run_policy(configured, binder, fake)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(configured.is_symlink())
            self.assertEqual(configured.lstat().st_ino, inode)
            self.assertEqual(os.readlink(configured), link_value)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o664)
            backups = list(directory.glob("operator-config.toml.before-qq-dictation.*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), original)
            self.assertEqual(list(directory.glob("config.toml.before-*")), [])

    def test_owned_marked_block_updates_only_a_prior_binder_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            configured, target, binder, fake, _log = self.fixture(directory)
            old = directory / "old" / "handy-remote-bind.py"
            text = base_config() + "\n" + herdr_policy.binder_block(old)
            target.write_text(text, encoding="utf-8")
            result = self.run_policy(configured, binder, fake)
            self.assertEqual(result.returncode, 0, result.stderr)
            current = target.read_text(encoding="utf-8")
            self.assertNotIn(str(old), current)
            self.assertIn(str(binder), current)
            self.assertEqual(current.count(herdr_policy.BEGIN_MARKER), 1)
            self.assertTrue(current.startswith(base_config()))

    def test_missing_unsafe_and_ambiguous_targets_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            fake, _log = fake_herdr(directory)
            binder = executable(directory / "handy-remote-bind.py", "#!/bin/sh\n")
            missing = directory / "missing.toml"
            result = self.run_policy(missing, binder, fake)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unavailable", result.stderr)

            unsafe = directory / "unsafe.toml"
            unsafe.write_text(base_config(), encoding="utf-8")
            unsafe.chmod(0o666)
            result = self.run_policy(unsafe, binder, fake)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("world-writable", result.stderr)
            self.assertEqual(unsafe.read_text(), base_config())

            link2 = directory / "link2.toml"
            link2.symlink_to(unsafe.name)
            link1 = directory / "link1.toml"
            link1.symlink_to(link2.name)
            result = self.run_policy(link1, binder, fake)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("symlink chain", result.stderr)
            self.assertTrue(link1.is_symlink())

            directory_target = directory / "directory.toml"
            directory_target.mkdir()
            result = self.run_policy(directory_target, binder, fake)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("regular file", result.stderr)

            owner_fixture = directory / "owner.toml"
            owner_fixture.write_text(base_config(), encoding="utf-8")
            with self.assertRaisesRegex(herdr_policy.PolicyError, "not owned"):
                herdr_policy.inspect_config_target(owner_fixture, os.geteuid() + 1)

    def test_duplicate_markers_occupied_chord_and_conflicting_command_refuse(self):
        cases = {
            "duplicate": base_config()
            + "\n"
            + herdr_policy.BEGIN_MARKER
            + "\n"
            + herdr_policy.BEGIN_MARKER
            + "\n"
            + herdr_policy.END_MARKER
            + "\n",
            "occupied action": base_config().replace(
                'new_tab = "prefix+c"', 'new_tab = "prefix+alt+d"'
            ),
            "occupied action array": base_config().replace(
                'new_tab = "prefix+c"',
                'new_tab = ["prefix+c", "prefix+alt+d"]',
            ),
            "occupied indexed key": base_config()
            + '\n[keys.indexed]\n"prefix+alt+d" = 4\n',
            "occupied indexed array": base_config()
            + '\n[keys.indexed]\nselect = ["prefix+1", "prefix+alt+d"]\n',
            "occupied command": base_config()
            + '\n[[keys.command]]\nkey = "prefix+alt+d"\ntype = "shell"\ncommand = "other"\n',
            "other chord": base_config()
            + '\n[[keys.command]]\nkey = "prefix+alt+x"\ntype = "shell"\ncommand = "/tmp/handy-remote-bind.py"\n',
            "marked conflict": base_config()
            + "\n"
            + herdr_policy.BEGIN_MARKER
            + '\n[[keys.command]]\nkey = "prefix+alt+d"\ntype = "shell"\ncommand = "/tmp/unrelated"\n'
            + herdr_policy.END_MARKER
            + "\n",
        }
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            fake, log = fake_herdr(directory)
            binder = executable(directory / "handy-remote-bind.py", "#!/bin/sh\n")
            for name, text in cases.items():
                with self.subTest(name=name):
                    target = directory / f"{name.replace(' ', '-')}.toml"
                    target.write_text(text, encoding="utf-8")
                    before = target.read_bytes()
                    result = self.run_policy(target, binder, fake)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(target.read_bytes(), before)
                    self.assertEqual(list(directory.glob(f"{target.name}.before-*")), [])
            self.assertFalse(log.exists())

    def test_nonconflicting_action_arrays_indexed_values_and_prefix_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            configured, target, binder, fake, _log = self.fixture(directory)
            original = (
                'onboarding = false\n\n'
                '[keys]\n'
                'prefix = "prefix+alt+d"\n'
                'new_tab = ["prefix+c", "prefix+n"]\n\n'
                '[keys.indexed]\n'
                'select = ["prefix+1", "prefix+2"]\n'
                '"prefix+3" = 3\n'
            )
            target.write_text(original, encoding="utf-8")
            result = self.run_policy(configured, binder, fake)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(target.read_text(encoding="utf-8").startswith(original))

    def test_candidate_validation_failure_occurs_before_target_mutation_or_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            configured, target, binder, fake, _log = self.fixture(directory)
            original = target.read_bytes()
            result = self.run_policy(
                configured, binder, fake, FAKE_HERDR_CHECK_FAIL="1"
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("rejected", result.stderr)
            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(list(directory.glob("*.before-qq-dictation.*")), [])
            self.assertEqual(list(directory.glob("*.candidate.*")), [])

    def test_postreplacement_identity_failure_restores_fixed_target_and_running_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            configured, target, binder, fake, log = self.fixture(directory, symlink=True)
            original = target.read_bytes()
            original_mode = stat.S_IMODE(target.stat().st_mode)
            link_inode = configured.lstat().st_ino
            link_value = os.readlink(configured)

            with mock.patch.object(
                herdr_policy,
                "verify_identity",
                side_effect=[
                    herdr_policy.PolicyError("injected post-replacement identity failure"),
                    None,
                ],
            ):
                with self.assertRaisesRegex(
                    herdr_policy.PolicyError, "target and running config rolled back"
                ):
                    herdr_policy.configure(configured, binder, fake)

            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), original_mode)
            self.assertEqual(configured.lstat().st_ino, link_inode)
            self.assertEqual(os.readlink(configured), link_value)
            backups = list(directory.glob("operator-config.toml.before-qq-dictation.*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), original)
            calls = [json.loads(line)["args"] for line in log.read_text().splitlines()]
            self.assertEqual(calls[-2:], [
                ["server", "reload-config"],
                ["status", "server", "--json"],
            ])

    def test_reload_and_postcheck_failures_atomically_restore_target_and_running_config(self):
        for variable in ("FAKE_HERDR_RELOAD_FAIL", "FAKE_HERDR_STATUS_FAIL"):
            with self.subTest(variable=variable), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                configured, target, binder, fake, log = self.fixture(directory, symlink=True)
                original = target.read_bytes()
                inode = configured.lstat().st_ino
                result = self.run_policy(configured, binder, fake, **{variable: "once"})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("rolled back", result.stderr)
                self.assertEqual(target.read_bytes(), original)
                self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o664)
                self.assertEqual(configured.lstat().st_ino, inode)
                backups = list(directory.glob("operator-config.toml.before-qq-dictation.*"))
                self.assertEqual(len(backups), 1)
                self.assertEqual(backups[0].read_bytes(), original)
                calls = [json.loads(line)["args"] for line in log.read_text().splitlines()]
                self.assertGreaterEqual(calls.count(["server", "reload-config"]), 2)
                self.assertEqual(calls[-1], ["status", "server", "--json"])


class InstallerTests(unittest.TestCase):
    def test_workstation_installer_places_helpers_modes_config_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            home = directory / "home"
            home.mkdir()
            config = home / ".config" / "herdr" / "config.toml"
            config.parent.mkdir(parents=True)
            config.write_text(base_config(), encoding="utf-8")
            fake, _log = fake_herdr(directory)
            local_bin = home / ".local" / "bin"
            local_bin.mkdir(parents=True)
            (local_bin / "handy-remote-stream.py").write_text("old", encoding="utf-8")
            command = [
                "/usr/bin/python3",
                str(WORKSTATION_INSTALLER),
                "--home",
                str(home),
                "--config",
                str(config),
                "--herdr",
                str(fake),
            ]
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            for name in ("handy-remote-stream.py", "handy-remote-bind.py"):
                installed = local_bin / name
                self.assertEqual(
                    installed.read_bytes(), (ROOT / "packaging" / name).read_bytes()
                )
                self.assertEqual(stat.S_IMODE(installed.stat().st_mode), 0o755)
            backups = list(local_bin.glob("handy-remote-stream.py.before-qq-dictation.*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), "old")
            self.assertIn("prefix+alt+d", config.read_text())

            backup_count = len(list(home.rglob("*.before-qq-dictation.*")))
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                len(list(home.rglob("*.before-qq-dictation.*"))), backup_count
            )

    def test_laptop_installer_installs_owner_config_service_modes_backups_and_restart(self):
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
            command = [
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
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            config = home / ".config" / "qq-dictation" / "remote-laptop.json"
            service = home / ".config" / "systemd" / "user" / "handy-remote-client.service"
            self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(client_path.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE(service.stat().st_mode), 0o644)
            value = json.loads(config.read_text())
            self.assertEqual(value["ssh_host"], "workstation")
            self.assertEqual(value["capture_argv"], [
                "/usr/bin/pw-record", "--rate", "16000", "--channels", "1", "--format", "s16", "-"
            ])
            self.assertNotIn("password", value)
            self.assertIn("ExecStart=/usr/bin/python3", service.read_text())
            self.assertEqual(
                systemctl_log.read_text().splitlines(),
                [
                    "--user daemon-reload",
                    "--user enable --now handy-remote-client.service",
                ],
            )
            backups = list(client_path.parent.glob("handy-remote-client.py.before-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), "old client")

            backup_count = len(list(home.rglob("*.before-qq-dictation.*")))
            # Existing config permits a normal argument-free idempotent rerun.
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

    def test_existing_laptop_config_must_be_mode_0600_before_any_mutation(self):
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
            initial = [
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
            result = subprocess.run(initial, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)

            config = home / ".config" / "qq-dictation" / "remote-laptop.json"
            client_path = home / ".local" / "bin" / "handy-remote-client.py"
            service = home / ".config" / "systemd" / "user" / "handy-remote-client.service"
            config.chmod(0o644)
            client_path.write_bytes(b"preserve existing client")
            client_path.chmod(0o755)
            service.write_bytes(b"preserve existing service")
            service.chmod(0o644)
            before = {
                config: config.read_bytes(),
                client_path: client_path.read_bytes(),
                service: service.read_bytes(),
            }
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

    def test_laptop_service_failure_is_reported_nonzero(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            home = directory / "home"
            home.mkdir()
            systemctl = executable(
                directory / "systemctl", "#!/bin/sh\necho synthetic systemctl failure >&2\nexit 1\n"
            )
            result = subprocess.run(
                [
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
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("synthetic systemctl failure", result.stderr)
            config = home / ".config" / "qq-dictation" / "remote-laptop.json"
            self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)

    def test_laptop_installer_failures_are_nonzero_and_do_not_overwrite_config(self):
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
                [
                    "/usr/bin/python3",
                    str(LAPTOP_INSTALLER),
                    "--home",
                    str(home),
                    "--ssh-host",
                    "new-host",
                    "--ghostty-title",
                    "title",
                    "--ghostty-class",
                    "class",
                    "--systemctl",
                    str(systemctl),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("differs", result.stderr)
            self.assertEqual(config.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
