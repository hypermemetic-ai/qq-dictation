#!/usr/bin/env python3
"""Run the installed qq-dictation AppDir against a private TASK-30 corpus."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from task30_common import (
    Task30Error,
    private_append_json_line,
    private_write_json,
    require_absolute,
    require_plain_directory,
    require_plain_file,
    selected_sample_ids,
    utc_now,
    validate_sample_files,
)


class BenchmarkError(Task30Error):
    pass


@dataclass(frozen=True)
class Arm:
    name: str
    model: str
    device_index: int

    def as_dict(self) -> dict[str, object]:
        return {"name": self.name, "model": self.model, "device_index": self.device_index}


@dataclass(frozen=True)
class BenchmarkConfig:
    installed_appdir: Path
    executable: Path
    corpus: Path
    output_dir: Path
    cohort: str
    warmup_rounds: int
    timed_rounds: int
    timeout_seconds: float
    arms: tuple[Arm, ...]


_BUILD_MARKERS = (
    "buildkit",
    "buildx",
    "docker build",
    "podman build",
    "cargo build",
    "cargo test",
    "rustc",
    "cmake",
    "ninja",
    "dpkg-buildpackage",
    "appimage-builder",
)
_CONTAINER_MARKERS = ("containerd", "dockerd", "podman", "buildkit")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    require_plain_file(path, label)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BenchmarkError(f"cannot parse {label}: {error}") from error
    if not isinstance(value, dict):
        raise BenchmarkError(f"{label} must contain a JSON object")
    return value


def _positive_int(value: Any, label: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise BenchmarkError(f"{label} must be an integer >= {minimum}")
    return value


def load_config(config_path: Path) -> BenchmarkConfig:
    raw = _load_json_object(config_path, "benchmark config")
    required = {
        "installed_appdir",
        "executable",
        "corpus",
        "output_dir",
        "cohort",
        "warmup_rounds",
        "timed_rounds",
        "arms",
    }
    missing = sorted(required.difference(raw))
    if missing:
        raise BenchmarkError("benchmark config is missing: " + ", ".join(missing))

    appdir = require_absolute(raw["installed_appdir"], "installed_appdir")
    executable = require_absolute(raw["executable"], "executable")
    corpus = require_absolute(raw["corpus"], "corpus")
    output_dir = require_absolute(raw["output_dir"], "output_dir")
    cohort = raw["cohort"]
    if cohort not in {"existing-saved", "full"}:
        raise BenchmarkError("cohort must be 'existing-saved' or 'full'")
    warmups = _positive_int(raw["warmup_rounds"], "warmup_rounds", 1)
    timed = _positive_int(raw["timed_rounds"], "timed_rounds", 5)
    timeout = raw.get("timeout_seconds", 600)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise BenchmarkError("timeout_seconds must be a positive number")

    arms_value = raw["arms"]
    if not isinstance(arms_value, list) or not arms_value:
        raise BenchmarkError("arms must be a non-empty array")
    arms: list[Arm] = []
    arm_names: set[str] = set()
    for index, value in enumerate(arms_value):
        if not isinstance(value, dict):
            raise BenchmarkError(f"arm {index + 1} must be an object")
        name = value.get("name")
        model = value.get("model")
        device = value.get("device_index")
        if not isinstance(name, str) or not name or any(character in name for character in "\r\n"):
            raise BenchmarkError(f"arm {index + 1} has an invalid name")
        if name in arm_names:
            raise BenchmarkError(f"duplicate arm name: {name}")
        if not isinstance(model, str) or not model or any(character in model for character in "\r\n"):
            raise BenchmarkError(f"arm {name} has an invalid model")
        if isinstance(device, bool) or not isinstance(device, int) or device < 0:
            raise BenchmarkError(f"arm {name} has an invalid device_index")
        arm_names.add(name)
        arms.append(Arm(name, model, device))

    return BenchmarkConfig(
        appdir,
        executable,
        corpus,
        output_dir,
        cohort,
        warmups,
        timed,
        float(timeout),
        tuple(arms),
    )


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_installed_app(config: BenchmarkConfig) -> str:
    require_plain_directory(config.installed_appdir, "installed_appdir")
    if not config.installed_appdir.name.endswith(".AppDir"):
        raise BenchmarkError("installed_appdir must name an installed .AppDir directory")
    executable_metadata = require_plain_file(config.executable, "installed AppDir executable")
    if not executable_metadata.st_mode & stat.S_IXUSR:
        raise BenchmarkError("installed AppDir executable is not owner-executable")
    appdir_real = config.installed_appdir.resolve()
    executable_real = config.executable.resolve()
    if not _is_within(executable_real, appdir_real):
        raise BenchmarkError("executable must be inside installed_appdir")

    repository_root = Path(__file__).resolve().parents[2]
    if _is_within(appdir_real, repository_root):
        raise BenchmarkError("refusing an AppDir inside the worktree; supply the installed product")

    commit_file = config.installed_appdir / "qq-dictation-commit"
    require_plain_file(commit_file, "installed qq-dictation-commit")
    try:
        commit = commit_file.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as error:
        raise BenchmarkError(f"cannot read installed qq-dictation-commit: {error}") from error
    if not _GIT_COMMIT.fullmatch(commit):
        raise BenchmarkError("installed qq-dictation-commit is not a 40-character Git commit")
    return commit


def _read_proc(path: str) -> dict[str, str | None]:
    try:
        return {"value": Path(path).read_text(encoding="utf-8", errors="replace").strip(), "error": None}
    except OSError as error:
        return {"value": None, "error": str(error)}


def _process_evidence() -> dict[str, object]:
    scanned = 0
    unreadable = 0
    build_matches: list[dict[str, object]] = []
    container_matches: list[dict[str, object]] = []
    proc = Path("/proc")
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command_bytes = (entry / "cmdline").read_bytes()
            command = command_bytes.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
            comm = (entry / "comm").read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            unreadable += 1
            continue
        scanned += 1
        haystack = f"{comm} {command}".lower()
        evidence = {"pid": int(entry.name), "comm": comm, "command": command}
        if any(marker in haystack for marker in _BUILD_MARKERS):
            build_matches.append(evidence)
        if any(marker in haystack for marker in _CONTAINER_MARKERS):
            container_matches.append(evidence)
    return {
        "scanned_processes": scanned,
        "unreadable_processes": unreadable,
        "build_markers": list(_BUILD_MARKERS),
        "build_like_processes": build_matches,
        "container_runtime_processes": container_matches,
        "self_cgroup": _read_proc("/proc/self/cgroup"),
        "init_cgroup": _read_proc("/proc/1/cgroup"),
        "self_mount_namespace": _readlink_or_error("/proc/self/ns/mnt"),
        "init_mount_namespace": _readlink_or_error("/proc/1/ns/mnt"),
    }


def _readlink_or_error(path: str) -> dict[str, str | None]:
    try:
        return {"value": os.readlink(path), "error": None}
    except OSError as error:
        return {"value": None, "error": str(error)}


def _run_process(argv: list[str], timeout: float) -> dict[str, object]:
    started_utc = utc_now()
    start = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return {
            "argv": argv,
            "started_utc": started_utc,
            "ended_utc": utc_now(),
            "elapsed_ms": round((time.monotonic() - start) * 1000, 3),
            "exit_status": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "launch_failure": None,
        }
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout or ""
        stderr = error.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return {
            "argv": argv,
            "started_utc": started_utc,
            "ended_utc": utc_now(),
            "elapsed_ms": round((time.monotonic() - start) * 1000, 3),
            "exit_status": None,
            "stdout": stdout,
            "stderr": stderr,
            "launch_failure": "timeout",
        }
    except OSError as error:
        return {
            "argv": argv,
            "started_utc": started_utc,
            "ended_utc": utc_now(),
            "elapsed_ms": round((time.monotonic() - start) * 1000, 3),
            "exit_status": None,
            "stdout": "",
            "stderr": "",
            "launch_failure": f"launch_error:{error}",
        }


def collect_machine_state(config: BenchmarkConfig, installed_commit: str) -> dict[str, object]:
    process_evidence = _process_evidence()
    device_argv = [str(config.executable), "--list-devices"]
    device_list = _run_process(device_argv, config.timeout_seconds)
    return {
        "captured_utc": utc_now(),
        "proc_loadavg": _read_proc("/proc/loadavg"),
        "proc_uptime": _read_proc("/proc/uptime"),
        "cpu_count": os.cpu_count(),
        "installed_appdir": str(config.installed_appdir),
        "executable": str(config.executable),
        "installed_qq_dictation_commit": installed_commit,
        "device_list": device_list,
        "process_container_evidence": process_evidence,
    }


def _valid_number(value: Any, *, positive: bool = False) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if not math.isfinite(float(value)):
        return False
    return value > 0 if positive else value >= 0


def validate_stdout_json(value: Any, arm: Arm) -> str | None:
    if not isinstance(value, dict):
        return "stdout_json_not_object"
    required = {
        "model",
        "requested_device",
        "bound_backend",
        "audio_secs",
        "load_ms",
        "transcribe_ms",
        "best_ms",
        "rtf",
        "text",
    }
    if not required.issubset(value):
        return "stdout_json_missing_fields"
    if value["model"] != arm.model:
        return "reported_model_mismatch"
    if value["requested_device"] != f"index {arm.device_index}":
        return "reported_device_mismatch"
    if not _valid_number(value["audio_secs"], positive=True):
        return "invalid_audio_secs"
    if value["bound_backend"] is not None and not isinstance(value["bound_backend"], str):
        return "invalid_bound_backend"
    if not _valid_number(value["load_ms"]):
        return "invalid_load_ms"
    times = value["transcribe_ms"]
    if not isinstance(times, list) or len(times) != 1 or not _valid_number(times[0], positive=True):
        return "invalid_single_transcribe_ms"
    if (
        not _valid_number(value["best_ms"], positive=True)
        or value["best_ms"] != times[0]
        or not _valid_number(value["rtf"])
    ):
        return "invalid_timing_fields"
    if not isinstance(value["text"], str):
        return "invalid_text"
    return None


def _benchmark_argv(config: BenchmarkConfig, arm: Arm, sample_id: str) -> list[str]:
    audio = config.corpus / "samples" / sample_id / "audio.wav"
    return [
        str(config.executable),
        "--transcribe-file",
        str(audio),
        "--model",
        arm.model,
        "--device-index",
        str(arm.device_index),
        "--repeat",
        "1",
        "--json",
    ]


def _observation(
    config: BenchmarkConfig,
    sequence: int,
    phase: str,
    round_number: int,
    arm: Arm,
    sample_id: str,
) -> dict[str, object]:
    process = _run_process(_benchmark_argv(config, arm, sample_id), config.timeout_seconds)
    parsed: object | None = None
    parse_error: str | None = None
    failure = process["launch_failure"]
    if failure is None:
        try:
            parsed = json.loads(str(process["stdout"]))
        except json.JSONDecodeError as error:
            parse_error = f"{error.msg} at line {error.lineno} column {error.colno}"
        if process["exit_status"] != 0:
            failure = "nonzero_exit"
        elif parse_error is not None:
            failure = "invalid_stdout_json"
        else:
            failure = validate_stdout_json(parsed, arm)
    return {
        "schema_version": 1,
        "sequence": sequence,
        "phase": phase,
        "round": round_number,
        "arm": arm.name,
        "sample_id": sample_id,
        "argv": process["argv"],
        "started_utc": process["started_utc"],
        "ended_utc": process["ended_utc"],
        "elapsed_ms": process["elapsed_ms"],
        "exit_status": process["exit_status"],
        "stdout_json": parsed,
        "stdout_json_parse_error": parse_error,
        "stdout": process["stdout"],
        "stderr": process["stderr"],
        "failure_mode": failure,
    }


def run_benchmark(config_path: Path) -> int:
    config = load_config(config_path)
    installed_commit = validate_installed_app(config)
    require_plain_directory(config.corpus, "corpus")
    sample_ids = selected_sample_ids(config.corpus, config.cohort)
    if not sample_ids:
        raise BenchmarkError("selected cohort is empty")
    validate_sample_files(config.corpus, sample_ids)
    if config.output_dir.exists() or config.output_dir.is_symlink():
        raise BenchmarkError(f"output_dir already exists: {config.output_dir}")
    config.output_dir.mkdir(mode=0o700, parents=True)
    config.output_dir.chmod(0o700)

    observations_path = config.output_dir / "observations.jsonl"
    observations_descriptor = os.open(
        observations_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND, 0o600
    )
    os.fchmod(observations_descriptor, 0o600)
    failures = 0
    observations = 0
    try:
        machine_state = collect_machine_state(config, installed_commit)
        run_manifest = {
            "schema_version": 1,
            "started_utc": machine_state["captured_utc"],
            "config_path": str(config_path),
            "installed_appdir": str(config.installed_appdir),
            "executable": str(config.executable),
            "installed_qq_dictation_commit": installed_commit,
            "corpus": str(config.corpus),
            "cohort": config.cohort,
            "warmup_rounds": config.warmup_rounds,
            "timed_rounds": config.timed_rounds,
            "timeout_seconds": config.timeout_seconds,
            "arms": [arm.as_dict() for arm in config.arms],
            "samples": sample_ids,
            "machine_state": machine_state,
        }
        private_write_json(config.output_dir / "run.json", run_manifest)

        device_list = machine_state["device_list"]
        if device_list["launch_failure"] is not None or device_list["exit_status"] != 0:
            private_write_json(
                config.output_dir / "completion.json",
                {
                    "completed_utc": utc_now(),
                    "status": "failed_device_list",
                    "observations": 0,
                    "failures": 1,
                },
            )
            return 1

        sequence = 0
        for sample_id in sample_ids:
            for phase, rounds in (
                ("warmup", config.warmup_rounds),
                ("timed", config.timed_rounds),
            ):
                for round_number in range(1, rounds + 1):
                    for arm in config.arms:
                        record = _observation(
                            config, sequence, phase, round_number, arm, sample_id
                        )
                        private_append_json_line(observations_descriptor, record)
                        observations += 1
                        if record["failure_mode"] is not None:
                            failures += 1
                        sequence += 1

        status = "complete" if failures == 0 else "complete_with_failures"
        private_write_json(
            config.output_dir / "completion.json",
            {
                "completed_utc": utc_now(),
                "status": status,
                "observations": observations,
                "failures": failures,
            },
        )
        return 0 if failures == 0 else 1
    finally:
        os.close(observations_descriptor)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run an installed qq-dictation AppDir using a private TASK-30 JSON config."
    )
    parser.add_argument("config", help="absolute path to benchmark JSON config")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config_path = require_absolute(args.config, "config")
        return run_benchmark(config_path)
    except (Task30Error, OSError) as error:
        print(f"benchmark failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
