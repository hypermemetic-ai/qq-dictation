"""Hermetic migration tests for the local Handy user service installer."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
INSTALLER = Path("ops/install/install-local.sh")
SERVICE = Path("ops/install/handy.service")
COPY_INPUTS = (
    INSTALLER,
    SERVICE,
    Path("ops/install/configure-local-settings.py"),
    Path("ops/install/install-remote-workstation.py"),
    Path("ops/install/handy-remote-stream.py"),
    Path("ops/package/handy"),
)


def executable(path: Path, content: str) -> Path:
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    path.chmod(0o755)
    return path


class LocalServiceInstallerTests(unittest.TestCase):
    def make_install(self, directory: Path):
        repository = directory / "repository"
        for relative in COPY_INPUTS:
            destination = repository / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)

        source_app = repository / ".local-build" / "Handy.AppDir"
        source_binary = source_app / "usr" / "bin" / "handy"
        source_binary.parent.mkdir(parents=True)
        executable(source_app / "AppRun", "#!/usr/bin/env bash\nexit 0\n")
        executable(source_binary, "#!/usr/bin/env bash\nexit 0\n")
        (source_app / "release-marker").write_text("replacement\n", encoding="utf-8")

        home = directory / "home"
        local_bin = home / ".local" / "bin"
        user_units = home / ".config" / "systemd" / "user"
        installed_app = home / ".local" / "opt" / "qq-dictation" / "Handy.AppDir"
        runtime = directory / "runtime"
        for path in (local_bin, user_units, installed_app, runtime):
            path.mkdir(parents=True)

        (installed_app / "release-marker").write_text("previous\n", encoding="utf-8")
        executable(local_bin / "handy", "#!/usr/bin/env bash\nexit 8\n")
        (local_bin / "handy-ptt-bridge.py").write_text(
            "active legacy bridge\n", encoding="utf-8"
        )
        (local_bin / "handy-ptt-bridge.py.before-qq-dictation").write_text(
            "retained bridge backup\n", encoding="utf-8"
        )
        (user_units / "handy-ptt.service").write_text(
            "active legacy unit\n", encoding="utf-8"
        )
        (user_units / "handy-ptt.service.before-qq-dictation").write_text(
            "retained unit backup\n", encoding="utf-8"
        )
        (user_units / "handy.service").write_text(
            "old direct unit\n", encoding="utf-8"
        )

        settings = home / ".local" / "share" / "com.pais.handy" / "settings_store.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(
            json.dumps(
                {
                    "settings": {
                        "selected_model": "operator-model",
                        "push_to_talk": False,
                    },
                    "operator_data": {"keep": True},
                }
            )
            + "\n",
            encoding="utf-8",
        )

        fake_bin = directory / "fake-bin"
        fake_bin.mkdir()
        state = directory / "process-state"
        command_log = directory / "systemctl.log"
        pgrep_log = directory / "pgrep.log"
        expected_executable = installed_app / "usr" / "bin" / "handy"
        legacy_bridge = local_bin / "handy-ptt-bridge.py"
        legacy_unit = user_units / "handy-ptt.service"
        direct_unit = user_units / "handy.service"
        marker = runtime / "qq-dictation-handy-ready"

        executable(
            fake_bin / "systemctl",
            f"""
            #!/usr/bin/python3
            import os
            from pathlib import Path
            import sys

            arguments = sys.argv[1:]
            with Path({str(command_log)!r}).open("a", encoding="utf-8") as stream:
                stream.write(" ".join(arguments) + "\\n")
            legacy = [Path({str(legacy_bridge)!r}), Path({str(legacy_unit)!r})]
            if arguments == ["--user", "disable", "handy-ptt.service"]:
                if any(path.exists() for path in legacy) != all(path.exists() for path in legacy):
                    print("legacy artifacts removed before disable completed", file=sys.stderr)
                    raise SystemExit(91)
            if arguments == ["--user", "stop", "handy.service"]:
                if not Path({str(installed_app)!r}).is_dir():
                    print("replacement service stopped after AppDir removal", file=sys.stderr)
                    raise SystemExit(92)
                Path({str(state)!r}).write_text("stopped\\n", encoding="utf-8")
            if arguments == ["--user", "daemon-reload"]:
                if any(path.exists() for path in legacy):
                    print("daemon reloaded before legacy artifact removal", file=sys.stderr)
                    raise SystemExit(93)
            if arguments == ["--user", "start", "handy.service"]:
                unit = Path({str(direct_unit)!r}).read_text(encoding="utf-8")
                if "ExecStart=%h/.local/bin/handy --start-hidden" not in unit:
                    print("direct hidden startup is not installed", file=sys.stderr)
                    raise SystemExit(94)
                Path({str(state)!r}).write_text("started\\n", encoding="utf-8")
                marker_pid = os.environ.get("FAKE_MARKER_PID", "4242")
                Path({str(marker)!r}).write_text(f"{{marker_pid}} ready\\n", encoding="utf-8")
            raise SystemExit(0)
            """,
        )
        executable(
            fake_bin / "pkill",
            f"""
            #!/usr/bin/python3
            from pathlib import Path
            Path({str(state)!r}).write_text("stopped\\n", encoding="utf-8")
            """,
        )
        executable(
            fake_bin / "pgrep",
            f"""
            #!/usr/bin/python3
            import os
            from pathlib import Path
            import sys
            with Path({str(pgrep_log)!r}).open("a", encoding="utf-8") as stream:
                stream.write(" ".join(sys.argv[1:]) + "\\n")
            state = Path({str(state)!r})
            if state.exists() and state.read_text(encoding="utf-8").strip() == "started":
                print(os.environ.get("FAKE_PIDS", "4242"))
                raise SystemExit(0)
            raise SystemExit(1)
            """,
        )
        executable(
            fake_bin / "readlink",
            f"""
            #!/usr/bin/python3
            import os
            print(os.environ.get("FAKE_EXECUTABLE", {str(expected_executable)!r}))
            """,
        )
        executable(fake_bin / "sleep", "#!/usr/bin/env bash\nexit 0\n")

        environment = os.environ.copy()
        environment.update(
            {
                "HOME": str(home),
                "PATH": f"{fake_bin}:/usr/bin:/bin",
                "XDG_RUNTIME_DIR": str(runtime),
            }
        )
        return {
            "repository": repository,
            "home": home,
            "local_bin": local_bin,
            "user_units": user_units,
            "installed_app": installed_app,
            "settings": settings,
            "environment": environment,
            "command_log": command_log,
            "pgrep_log": pgrep_log,
        }

    def run_installer(self, fixture, **environment_updates):
        environment = fixture["environment"].copy()
        environment.update(environment_updates)
        return subprocess.run(
            ["/usr/bin/bash", str(fixture["repository"] / INSTALLER)],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_service_runs_the_launcher_hidden_without_a_bridge(self):
        service = (ROOT / SERVICE).read_text(encoding="utf-8")
        self.assertIn("ExecStart=%h/.local/bin/handy --start-hidden", service)
        self.assertIn("WantedBy=default.target", service)
        self.assertIn("Restart=on-failure", service)
        self.assertNotIn("python", service.lower())
        self.assertNotIn("bridge", service.lower())

        installer = (ROOT / INSTALLER).read_text(encoding="utf-8")
        self.assertNotIn("import Xlib", installer)
        self.assertNotIn("/usr/bin/setsid", installer)
        self.assertNotIn("enable --now handy-ptt.service", installer)

    def test_migrates_legacy_install_and_repeats_without_changing_data_policy(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.make_install(Path(temporary))
            local_bin = fixture["local_bin"]
            user_units = fixture["user_units"]

            for _ in range(2):
                result = self.run_installer(fixture)
                self.assertEqual(result.returncode, 0, result.stderr)

            commands = fixture["command_log"].read_text(encoding="utf-8").splitlines()
            expected_cycle = [
                "--user stop handy-ptt.service",
                "--user disable handy-ptt.service",
                "--user stop handy.service",
                "--user daemon-reload",
                "--user enable handy.service",
                "--user start handy.service",
            ]
            self.assertEqual(commands, expected_cycle * 2)
            self.assertNotIn("--user enable handy-ptt.service", commands)

            self.assertFalse((local_bin / "handy-ptt-bridge.py").exists())
            self.assertFalse((user_units / "handy-ptt.service").exists())
            self.assertEqual(
                (local_bin / "handy-ptt-bridge.py.before-qq-dictation").read_text(
                    encoding="utf-8"
                ),
                "retained bridge backup\n",
            )
            self.assertEqual(
                (user_units / "handy-ptt.service.before-qq-dictation").read_text(
                    encoding="utf-8"
                ),
                "retained unit backup\n",
            )
            self.assertEqual(
                (user_units / "handy.service").read_bytes(),
                (ROOT / SERVICE).read_bytes(),
            )
            self.assertEqual(
                (user_units / "handy.service.before-qq-dictation").read_text(
                    encoding="utf-8"
                ),
                "old direct unit\n",
            )
            self.assertEqual(
                (fixture["installed_app"] / "release-marker").read_text(
                    encoding="utf-8"
                ),
                "replacement\n",
            )
            self.assertGreaterEqual(
                len(list(fixture["installed_app"].parent.glob("Handy.AppDir.backup.*"))),
                2,
            )

            settings = json.loads(fixture["settings"].read_text(encoding="utf-8"))
            self.assertEqual(settings["settings"]["selected_model"], "operator-model")
            self.assertIs(settings["settings"]["push_to_talk"], True)
            self.assertEqual(settings["operator_data"], {"keep": True})
            self.assertTrue(
                list(fixture["settings"].parent.glob("settings_store.json.before-*"))
            )
            self.assertTrue((local_bin / "handy-remote-stream.py").is_file())
            self.assertTrue(
                stat.S_IMODE((local_bin / "handy-remote-stream.py").stat().st_mode)
                == 0o755
            )
            self.assertIn("-u", fixture["pgrep_log"].read_text(encoding="utf-8"))

    def test_rejects_a_readiness_marker_for_another_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.make_install(Path(temporary))
            result = self.run_installer(fixture, FAKE_MARKER_PID="41")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("readiness marker does not match running pid 4242", result.stderr)

    def test_rejects_multiple_running_handy_processes(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.make_install(Path(temporary))
            result = self.run_installer(fixture, FAKE_PIDS="4242\n4343")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Expected exactly one running Handy process; found 2", result.stderr)

    def test_rejects_a_running_binary_outside_the_installed_appdir(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.make_install(Path(temporary))
            result = self.run_installer(
                fixture, FAKE_EXECUTABLE="/tmp/unexpected/usr/bin/handy"
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Unexpected Handy executable after installation", result.stderr)


if __name__ == "__main__":
    unittest.main()
