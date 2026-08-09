#!/usr/bin/env python3
"""Validate and summarize a private TASK-30 benchmark run."""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from task30_common import (
    Task30Error,
    private_write_bytes,
    require_absolute,
    require_plain_directory,
    require_plain_file,
)


class SummaryError(Task30Error):
    pass


@dataclass(frozen=True)
class SuccessfulTimed:
    sequence: int
    arm: str
    model: str
    sample_id: str
    audio_secs: float
    transcribe_ms: float
    load_ms: float
    text: str
    stderr: str

    @property
    def realtime_multiple(self) -> float:
        return self.audio_secs / (self.transcribe_ms / 1000.0)


_SUPPORTS_STREAMING = re.compile(r"\bsupports_streaming=(true|false)\b", re.IGNORECASE)
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    require_plain_file(path, label)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SummaryError(f"cannot parse {label}: {error}") from error
    if not isinstance(value, dict):
        raise SummaryError(f"{label} must contain a JSON object")
    return value


def _load_json_lines(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    require_plain_file(path, "observations.jsonl")
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise SummaryError(f"cannot read observations.jsonl: {error}") from error
    for line_number, line in enumerate(lines, 1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            errors.append(f"observation line {line_number} is invalid JSON: {error.msg}")
            continue
        if not isinstance(value, dict):
            errors.append(f"observation line {line_number} is not an object")
            continue
        records.append(value)
    return records, errors


def _canonical_arms(manifest: dict[str, Any], errors: list[str]) -> list[dict[str, Any]]:
    value = manifest.get("arms")
    if not isinstance(value, list) or not value:
        errors.append("run manifest arms must be a non-empty array")
        return []
    arms: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, arm in enumerate(value, 1):
        if not isinstance(arm, dict):
            errors.append(f"run manifest arm {index} is not an object")
            continue
        name, model, device = arm.get("name"), arm.get("model"), arm.get("device_index")
        if (
            not isinstance(name, str)
            or not name
            or name in names
            or not isinstance(model, str)
            or not model
            or isinstance(device, bool)
            or not isinstance(device, int)
            or device < 0
        ):
            errors.append(f"run manifest arm {index} is invalid")
            continue
        names.add(name)
        arms.append({"name": name, "model": model, "device_index": device})
    return arms


def _canonical_samples(manifest: dict[str, Any], errors: list[str]) -> list[str]:
    value = manifest.get("samples")
    if not isinstance(value, list) or not value:
        errors.append("run manifest samples must be a non-empty array")
        return []
    samples: list[str] = []
    for index, sample in enumerate(value, 1):
        if not isinstance(sample, str) or not sample.isascii() or not sample.isdigit() or int(sample) <= 0:
            errors.append(f"run manifest sample {index} is invalid")
            continue
        if str(int(sample)) != sample or sample in samples:
            errors.append(f"run manifest sample {index} is duplicate or non-canonical")
            continue
        samples.append(sample)
    return samples


def _expected_schedule(
    manifest: dict[str, Any], arms: list[dict[str, Any]], samples: list[str], errors: list[str]
) -> list[tuple[str, int, str, str]]:
    warmups = manifest.get("warmup_rounds")
    timed = manifest.get("timed_rounds")
    if isinstance(warmups, bool) or not isinstance(warmups, int) or warmups < 1:
        errors.append("run manifest warmup_rounds must be >= 1")
        warmups = 0
    if isinstance(timed, bool) or not isinstance(timed, int) or timed < 5:
        errors.append("run manifest timed_rounds must be >= 5")
        timed = 0
    schedule: list[tuple[str, int, str, str]] = []
    for sample_id in samples:
        for phase, rounds in (("warmup", warmups), ("timed", timed)):
            for round_number in range(1, rounds + 1):
                for arm in arms:
                    schedule.append((phase, round_number, arm["name"], sample_id))
    return schedule


def _number(value: Any, *, positive: bool = False) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    if not math.isfinite(result) or (result <= 0 if positive else result < 0):
        return None
    return result


def validate_run(
    manifest: dict[str, Any], observations: list[dict[str, Any]], initial_errors: Iterable[str] = ()
) -> tuple[list[str], list[dict[str, Any]], list[str], list[SuccessfulTimed]]:
    errors = list(initial_errors)
    if manifest.get("schema_version") != 1:
        errors.append("run manifest schema_version must be 1")
    commit = manifest.get("installed_qq_dictation_commit")
    if not isinstance(commit, str) or not _GIT_COMMIT.fullmatch(commit):
        errors.append("run manifest installed commit is invalid")
    machine_state = manifest.get("machine_state")
    if not isinstance(machine_state, dict):
        errors.append("run manifest machine_state must be an object")
        machine_state = {}
    for field in (
        "proc_loadavg",
        "proc_uptime",
        "cpu_count",
        "installed_qq_dictation_commit",
        "device_list",
        "process_container_evidence",
    ):
        if field not in machine_state:
            errors.append(f"run manifest machine_state lacks {field}")
    if machine_state.get("installed_qq_dictation_commit") != commit:
        errors.append("machine state installed commit differs from run manifest")
    executable = manifest.get("executable")
    corpus_value = manifest.get("corpus")
    if not isinstance(executable, str) or not Path(executable).is_absolute():
        errors.append("run manifest executable must be absolute")
        executable = ""
    if not isinstance(corpus_value, str) or not Path(corpus_value).is_absolute():
        errors.append("run manifest corpus must be absolute")
        corpus_value = ""
    device_list = machine_state.get("device_list")
    if isinstance(device_list, dict) and device_list.get("argv") != [executable, "--list-devices"]:
        errors.append("machine-state device-list argv is invalid")
    arms = _canonical_arms(manifest, errors)
    samples = _canonical_samples(manifest, errors)
    schedule = _expected_schedule(manifest, arms, samples, errors)
    arm_by_name = {arm["name"]: arm for arm in arms}

    if len(observations) != len(schedule):
        errors.append(
            f"observation count {len(observations)} does not match expected {len(schedule)}"
        )
    successful: list[SuccessfulTimed] = []
    for sequence, expected in enumerate(schedule):
        if sequence >= len(observations):
            break
        record = observations[sequence]
        phase, round_number, arm_name, sample_id = expected
        identity = (record.get("phase"), record.get("round"), record.get("arm"), record.get("sample_id"))
        if record.get("sequence") != sequence:
            errors.append(f"observation {sequence} has a non-canonical sequence")
        if identity != expected:
            errors.append(f"observation {sequence} breaks sample/round/arm interleaving")
            continue
        arm = arm_by_name[arm_name]
        expected_argv = [
            executable,
            "--transcribe-file",
            str(Path(corpus_value) / "samples" / sample_id / "audio.wav"),
            "--model",
            arm["model"],
            "--device-index",
            str(arm["device_index"]),
            "--repeat",
            "1",
            "--json",
        ]
        if record.get("argv") != expected_argv:
            errors.append(f"observation {sequence} argv does not match the approved invocation")
        if record.get("schema_version") != 1:
            errors.append(f"observation {sequence} schema_version must be 1")
        for text_field in ("started_utc", "ended_utc", "stdout", "stderr"):
            if not isinstance(record.get(text_field), str):
                errors.append(f"observation {sequence} has invalid {text_field}")
        exit_status = record.get("exit_status")
        if exit_status is not None and (isinstance(exit_status, bool) or not isinstance(exit_status, int)):
            errors.append(f"observation {sequence} has invalid exit_status")
        failure = record.get("failure_mode")
        if failure is not None:
            if not isinstance(failure, str) or not failure:
                errors.append(f"observation {sequence} has an invalid failure_mode")
            continue
        if record.get("exit_status") != 0:
            errors.append(f"observation {sequence} claims success with a nonzero exit")
            continue
        result = record.get("stdout_json")
        if not isinstance(result, dict):
            errors.append(f"observation {sequence} success lacks parsed stdout JSON")
            continue
        required_result_fields = {
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
        if not required_result_fields.issubset(result):
            errors.append(f"observation {sequence} parsed stdout JSON lacks required fields")
            continue
        if result.get("model") != arm["model"] or result.get("requested_device") != f"index {arm['device_index']}":
            errors.append(f"observation {sequence} reports the wrong model or device")
            continue
        bound_backend = result.get("bound_backend")
        audio_secs = _number(result.get("audio_secs"), positive=True)
        load_ms = _number(result.get("load_ms"))
        best_ms = _number(result.get("best_ms"), positive=True)
        reported_rtf = _number(result.get("rtf"))
        transcribe = result.get("transcribe_ms")
        transcribe_ms = (
            _number(transcribe[0], positive=True)
            if isinstance(transcribe, list) and len(transcribe) == 1
            else None
        )
        text = result.get("text")
        stderr = record.get("stderr")
        if (
            audio_secs is None
            or load_ms is None
            or best_ms is None
            or reported_rtf is None
            or transcribe_ms is None
            or best_ms != transcribe_ms
            or (bound_backend is not None and not isinstance(bound_backend, str))
            or not isinstance(text, str)
            or not isinstance(stderr, str)
        ):
            errors.append(f"observation {sequence} has invalid successful output fields")
            continue
        if phase == "timed":
            successful.append(
                SuccessfulTimed(
                    sequence,
                    arm_name,
                    arm["model"],
                    sample_id,
                    audio_secs,
                    transcribe_ms,
                    load_ms,
                    text,
                    stderr,
                )
            )

    if len(observations) > len(schedule):
        errors.append("raw run contains observations beyond the approved schedule")
    return errors, arms, samples, successful


def _format_number(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def _stats(rows: list[SuccessfulTimed]) -> tuple[float | None, float | None, float | None, float | None]:
    if not rows:
        return None, None, None, None
    multiples = [row.realtime_multiple for row in rows]
    loads = [row.load_ms for row in rows]
    return max(multiples), statistics.median(multiples), min(loads), statistics.median(loads)


def _markdown_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _failure_rows(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [record for record in observations if record.get("failure_mode") is not None]


def _streaming_by_arm(
    arms: list[dict[str, Any]], observations: list[dict[str, Any]]
) -> dict[str, set[str]]:
    values = {arm["name"]: set() for arm in arms}
    for record in observations:
        arm = record.get("arm")
        stderr = record.get("stderr")
        if arm in values and isinstance(stderr, str):
            values[arm].update(match.lower() for match in _SUPPORTS_STREAMING.findall(stderr))
    return values


def build_summary_markdown(
    manifest: dict[str, Any],
    observations: list[dict[str, Any]],
    validation_errors: list[str],
    arms: list[dict[str, Any]],
    samples: list[str],
    successful: list[SuccessfulTimed],
) -> str:
    lines = [
        "# TASK-30 benchmark summary",
        "",
        "This report compares installed-product observations. It does **not** claim absolute WER or semantic quality; the differential review sheet requires operator adjudication.",
        "",
        f"- Installed commit: `{_markdown_cell(manifest.get('installed_qq_dictation_commit', 'unknown'))}`",
        f"- Cohort: `{_markdown_cell(manifest.get('cohort', 'unknown'))}` ({len(samples)} files)",
        f"- Warm-up rounds (excluded below): {manifest.get('warmup_rounds', 'unknown')}",
        f"- Timed rounds: {manifest.get('timed_rounds', 'unknown')}",
        "",
        "## Raw-run validation",
        "",
    ]
    if validation_errors:
        lines.append(f"**INVALID ({len(validation_errors)} issue(s))**")
        lines.extend(f"- {_markdown_cell(error)}" for error in validation_errors)
    else:
        lines.append("Valid: all expected invocations are present in exact sample/round/listed-arm order with exact argv.")

    lines.extend(
        [
            "",
            "## Per-arm timed results",
            "",
            "| Arm | Timed successes | Best real-time multiple | Median real-time multiple | Best cold load (ms) | Median cold load (ms) |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for arm in arms:
        rows = [row for row in successful if row.arm == arm["name"]]
        best_rtm, median_rtm, best_load, median_load = _stats(rows)
        lines.append(
            f"| {_markdown_cell(arm['name'])} | {len(rows)} | {_format_number(best_rtm)} | {_format_number(median_rtm)} | {_format_number(best_load)} | {_format_number(median_load)} |"
        )

    lines.extend(
        [
            "",
            "## Per-arm, per-file timed results",
            "",
            "| Arm | Sample | Timed successes | Best real-time multiple | Median real-time multiple | Best cold load (ms) | Median cold load (ms) | Output variants |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for arm in arms:
        for sample_id in samples:
            rows = [
                row for row in successful if row.arm == arm["name"] and row.sample_id == sample_id
            ]
            best_rtm, median_rtm, best_load, median_load = _stats(rows)
            variants = len({row.text for row in rows})
            lines.append(
                f"| {_markdown_cell(arm['name'])} | {sample_id} | {len(rows)} | {_format_number(best_rtm)} | {_format_number(median_rtm)} | {_format_number(best_load)} | {_format_number(median_load)} | {variants} |"
            )

    streaming = _streaming_by_arm(arms, observations)
    lines.extend(
        [
            "",
            "## Observed load-log streaming support",
            "",
            "| Arm | Observed `supports_streaming` |",
            "| --- | --- |",
        ]
    )
    for arm in arms:
        values = streaming[arm["name"]]
        display = "not observed" if not values else ", ".join(sorted(values))
        if len(values) > 1:
            display = "NONDETERMINISTIC: " + display
        lines.append(f"| {_markdown_cell(arm['name'])} | {display} |")

    failures = _failure_rows(observations)
    lines.extend(
        [
            "",
            f"## Failures ({len(failures)})",
            "",
            "| Sequence | Phase | Round | Arm | Sample | Exit | Failure mode |",
            "| ---: | --- | ---: | --- | --- | ---: | --- |",
        ]
    )
    for record in failures:
        lines.append(
            "| {sequence} | {phase} | {round} | {arm} | {sample} | {exit_status} | {failure} |".format(
                sequence=_markdown_cell(record.get("sequence", "?")),
                phase=_markdown_cell(record.get("phase", "?")),
                round=_markdown_cell(record.get("round", "?")),
                arm=_markdown_cell(record.get("arm", "?")),
                sample=_markdown_cell(record.get("sample_id", "?")),
                exit_status=_markdown_cell(record.get("exit_status", "—")),
                failure=_markdown_cell(record.get("failure_mode", "?")),
            )
        )
    if not failures:
        lines.append("| — | — | — | — | — | — | none |")

    nondeterministic = []
    for arm in arms:
        for sample_id in samples:
            rows = [
                row for row in successful if row.arm == arm["name"] and row.sample_id == sample_id
            ]
            counts = Counter(row.text for row in rows)
            if len(counts) > 1:
                nondeterministic.append((arm["name"], sample_id, sorted(counts.values(), reverse=True)))
    lines.extend(["", f"## Nondeterministic outputs ({len(nondeterministic)})", ""])
    if nondeterministic:
        lines.extend(
            [
                "Every distinct timed variant is a separate row in `differential-review.csv`; none was silently selected.",
                "",
                "| Arm | Sample | Variant count | Timed observations per variant |",
                "| --- | --- | ---: | --- |",
            ]
        )
        for arm_name, sample_id, counts in nondeterministic:
            lines.append(
                f"| {_markdown_cell(arm_name)} | {sample_id} | {len(counts)} | {', '.join(map(str, counts))} |"
            )
    else:
        lines.append("No timed arm/file output variants were observed.")
    lines.append("")
    return "\n".join(lines)


def _production_transcript(corpus: Path, sample_id: str) -> str:
    metadata_path = corpus / "samples" / sample_id / "metadata.json"
    metadata = _load_json_object(metadata_path, f"sample {sample_id} metadata")
    production = metadata.get("production_transcript")
    if not isinstance(production, str):
        raise SummaryError(f"sample {sample_id} metadata lacks production_transcript")
    return production


def build_review_csv(
    corpus: Path,
    arms: list[dict[str, Any]],
    samples: list[str],
    successful: list[SuccessfulTimed],
) -> str:
    output = io.StringIO(newline="")
    fieldnames = [
        "sample_id",
        "arm",
        "model",
        "status",
        "variant_index",
        "variant_count",
        "timed_observation_count",
        "production_transcript",
        "candidate_text",
        "materiality",
        "preference",
        "notes",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for sample_id in samples:
        production = _production_transcript(corpus, sample_id)
        for arm in arms:
            rows = [
                row for row in successful if row.arm == arm["name"] and row.sample_id == sample_id
            ]
            counts = Counter(row.text for row in rows)
            ordered_variants = sorted(counts, key=lambda text: min(row.sequence for row in rows if row.text == text))
            if not ordered_variants:
                writer.writerow(
                    {
                        "sample_id": sample_id,
                        "arm": arm["name"],
                        "model": arm["model"],
                        "status": "no successful timed output",
                        "variant_index": "",
                        "variant_count": 0,
                        "timed_observation_count": 0,
                        "production_transcript": production,
                        "candidate_text": "",
                        "materiality": "",
                        "preference": "",
                        "notes": "",
                    }
                )
                continue
            for variant_index, text in enumerate(ordered_variants, 1):
                writer.writerow(
                    {
                        "sample_id": sample_id,
                        "arm": arm["name"],
                        "model": arm["model"],
                        "status": "candidate output",
                        "variant_index": variant_index,
                        "variant_count": len(ordered_variants),
                        "timed_observation_count": counts[text],
                        "production_transcript": production,
                        "candidate_text": text,
                        "materiality": "",
                        "preference": "",
                        "notes": "",
                    }
                )
    return output.getvalue()


def summarize(run_dir: Path, output_dir: Path) -> tuple[list[str], Path, Path]:
    require_plain_directory(run_dir, "run directory")
    if output_dir.exists() or output_dir.is_symlink():
        raise SummaryError(f"output directory already exists: {output_dir}")
    manifest = _load_json_object(run_dir / "run.json", "run.json")
    observations, line_errors = _load_json_lines(run_dir / "observations.jsonl")
    validation_errors, arms, samples, successful = validate_run(
        manifest, observations, line_errors
    )
    corpus_value = manifest.get("corpus")
    if not isinstance(corpus_value, str) or not Path(corpus_value).is_absolute():
        raise SummaryError("run manifest has no usable absolute corpus path")
    corpus = Path(corpus_value)
    require_plain_directory(corpus, "corpus")

    summary = build_summary_markdown(
        manifest, observations, validation_errors, arms, samples, successful
    )
    review = build_review_csv(corpus, arms, samples, successful)

    output_dir.mkdir(mode=0o700, parents=True)
    output_dir.chmod(0o700)
    summary_path = output_dir / "summary.md"
    review_path = output_dir / "differential-review.csv"
    private_write_bytes(summary_path, summary.encode("utf-8"))
    private_write_bytes(review_path, review.encode("utf-8"))
    return validation_errors, summary_path, review_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and summarize private TASK-30 raw benchmark output."
    )
    parser.add_argument("run_dir", help="absolute private raw run directory")
    parser.add_argument("--output", required=True, help="absolute new private report directory")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        run_dir = require_absolute(args.run_dir, "run_dir")
        output_dir = require_absolute(args.output, "output")
        validation_errors, summary_path, review_path = summarize(run_dir, output_dir)
        print(f"summary: {summary_path}")
        print(f"differential review: {review_path}")
        if validation_errors:
            print(
                f"raw run validation failed with {len(validation_errors)} issue(s)",
                file=sys.stderr,
            )
            return 1
        return 0
    except (Task30Error, OSError) as error:
        print(f"summarization failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
