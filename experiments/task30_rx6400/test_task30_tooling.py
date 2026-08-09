from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import stat
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

import collect_corpus
import run_benchmark
import summarize_results


SYNTHETIC_COMMIT = "a" * 40
SYNTHETIC_MODEL = "synthetic/model/synthetic-Q1.gguf"


def write_wav(path: Path, frames: int = 160) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\x00\x00" * frames)


def create_history_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE transcription_history ("
        "id INTEGER PRIMARY KEY, file_name TEXT NOT NULL, timestamp INTEGER NOT NULL, "
        "transcription_text TEXT NOT NULL, post_processed_text TEXT, "
        "audio_available BOOLEAN NOT NULL DEFAULT 1)"
    )
    connection.commit()
    return connection


def insert_history(
    connection: sqlite3.Connection,
    history_id: int,
    file_name: str,
    raw: str,
    processed: str | None,
) -> None:
    connection.execute(
        "INSERT INTO transcription_history "
        "(id, file_name, timestamp, transcription_text, post_processed_text, audio_available) "
        "VALUES (?, ?, ?, ?, ?, 1)",
        (history_id, file_name, 1_700_000_000 + history_id, raw, processed),
    )
    connection.commit()


def create_corpus(root: Path, sample_ids: tuple[str, ...] = ("1",)) -> Path:
    corpus = root / "corpus"
    samples = corpus / "samples"
    samples.mkdir(parents=True)
    for sample_id in sample_ids:
        sample = samples / sample_id
        sample.mkdir()
        write_wav(sample / "audio.wav")
        (sample / "metadata.json").write_text(
            json.dumps(
                {
                    "raw_transcript": f"invented raw {sample_id}",
                    "post_processed_transcript": None,
                    "production_transcript": f"invented raw {sample_id}",
                }
            ),
            encoding="utf-8",
        )
    (corpus / "seed-existing.json").write_text(
        json.dumps({"cohort": "existing-saved", "history_ids": [int(item) for item in sample_ids]}),
        encoding="utf-8",
    )
    return corpus


def create_fake_app(root: Path) -> tuple[Path, Path]:
    appdir = root / "installed" / "Handy.AppDir"
    appdir.mkdir(parents=True)
    (appdir / "qq-dictation-commit").write_text(SYNTHETIC_COMMIT + "\n", encoding="ascii")
    executable = appdir / "AppRun"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

if '--list-devices' in sys.argv:
    print('synthetic devices: index 0, index 1')
    print('synthetic device enumeration', file=sys.stderr)
    raise SystemExit(0)

def argument(name):
    return sys.argv[sys.argv.index(name) + 1]

sample_id = Path(argument('--transcribe-file')).parent.name
device = int(argument('--device-index'))
model = argument('--model')
if os.environ.get('TASK30_FAKE_FAIL_DEVICE') == str(device):
    print('synthetic arm failure', file=sys.stderr)
    raise SystemExit(9)

state_path = Path(os.environ['TASK30_FAKE_STATE'])
state = json.loads(state_path.read_text()) if state_path.exists() else {}
key = f'{sample_id}:{device}'
invocation = state.get(key, 0) + 1
state[key] = invocation
state_path.write_text(json.dumps(state))
transcribe_values = [1, 100, 200, 300, 400, 500]
load_values = [1, 50, 40, 30, 20, 10]
position = min(invocation, len(transcribe_values)) - 1
transcribe_ms = transcribe_values[position]
load_ms = load_values[position]
if invocation == 1:
    text = 'invented warmup only'
elif invocation % 2 == 0:
    text = 'invented candidate variant alpha'
else:
    text = 'invented candidate variant beta'
streaming = 'true' if device == 0 else 'false'
print(f'loaded synthetic model supports_streaming={streaming}', file=sys.stderr)
print(json.dumps({
    'model': model,
    'requested_device': f'index {device}',
    'bound_backend': f'synthetic-{device}',
    'audio_secs': 2.0,
    'load_ms': load_ms,
    'transcribe_ms': [transcribe_ms],
    'best_ms': transcribe_ms,
    'rtf': 2.0 / (transcribe_ms / 1000.0),
    'text': text,
}))
""",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    return appdir, executable


def write_benchmark_config(
    path: Path,
    appdir: Path,
    executable: Path,
    corpus: Path,
    output: Path,
    arms: list[dict[str, object]],
) -> None:
    path.write_text(
        json.dumps(
            {
                "installed_appdir": str(appdir),
                "executable": str(executable),
                "corpus": str(corpus),
                "output_dir": str(output),
                "cohort": "existing-saved",
                "warmup_rounds": 1,
                "timed_rounds": 5,
                "timeout_seconds": 30,
                "arms": arms,
            }
        ),
        encoding="utf-8",
    )


class CollectorTests(unittest.TestCase):
    def test_idempotency_cohort_preservation_transcript_selection_and_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recordings = root / "recordings"
            recordings.mkdir()
            database = root / "history.db"
            connection = create_history_database(database)
            self.addCleanup(connection.close)
            insert_history(connection, 1, "invented-one.wav", "invented raw one", None)
            write_wav(recordings / "invented-one.wav")

            corpus = root / "private" / "corpus"
            corpus.mkdir(parents=True)
            seed_payload = b'{"cohort":"existing-saved","history_ids":[1]}\n'
            (corpus / "seed-existing.json").write_bytes(seed_payload)

            first = collect_corpus.collect_once(database, recordings, corpus)
            self.assertEqual((first.newly_published, first.already_present), (1, 0))
            self.assertEqual((first.existing_saved, first.fresh), (1, 0))
            self.assertEqual((corpus / "seed-existing.json").read_bytes(), seed_payload)
            metadata_one = json.loads(
                (corpus / "samples" / "1" / "metadata.json").read_text(encoding="utf-8")
            )
            self.assertEqual(metadata_one["production_transcript"], "invented raw one")

            insert_history(
                connection,
                2,
                "invented-two.wav",
                "invented raw two",
                "invented processed two",
            )
            write_wav(recordings / "invented-two.wav")
            second = collect_corpus.collect_once(database, recordings, corpus)
            self.assertEqual((second.newly_published, second.already_present), (1, 1))
            self.assertEqual((second.existing_saved, second.fresh), (1, 1))
            metadata_two = json.loads(
                (corpus / "samples" / "2" / "metadata.json").read_text(encoding="utf-8")
            )
            self.assertEqual(metadata_two["production_transcript"], "invented processed two")

            third = collect_corpus.collect_once(database, recordings, corpus)
            self.assertEqual((third.newly_published, third.already_present), (0, 2))
            self.assertEqual((third.existing_saved, third.fresh), (1, 1))
            self.assertEqual((corpus / "seed-existing.json").read_bytes(), seed_payload)

            for directory in (corpus, corpus / "samples", corpus / "samples" / "1", corpus / "samples" / "2"):
                self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            for private_file in (
                corpus / "seed-existing.json",
                corpus / "samples" / "1" / "audio.wav",
                corpus / "samples" / "1" / "metadata.json",
                corpus / "samples" / "2" / "audio.wav",
                corpus / "samples" / "2" / "metadata.json",
            ):
                self.assertEqual(stat.S_IMODE(private_file.stat().st_mode), 0o600)
            self.assertEqual(
                connection.execute("SELECT transcription_text FROM transcription_history WHERE id=1").fetchone()[0],
                "invented raw one",
            )

    def test_changed_and_conflicting_sources_never_publish_partial_samples(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recordings = root / "recordings"
            recordings.mkdir()
            database = root / "history.db"
            connection = create_history_database(database)
            self.addCleanup(connection.close)
            insert_history(connection, 1, "invented.wav", "invented changed raw", None)
            source = recordings / "invented.wav"
            write_wav(source, frames=2048)
            corpus = root / "corpus"

            original_stream_copy = collect_corpus._stream_copy

            def mutate_after_copy(source_file: object, destination_file: object) -> None:
                original_stream_copy(source_file, destination_file)
                with source.open("ab") as changed:
                    changed.write(b"invented-change")

            with mock.patch.object(collect_corpus, "_stream_copy", mutate_after_copy):
                with self.assertRaises(collect_corpus.SourceChanged):
                    collect_corpus.collect_once(database, recordings, corpus)
            self.assertFalse((corpus / "samples" / "1").exists())
            self.assertEqual(list((corpus / "samples").iterdir()), [])

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recordings = root / "recordings"
            recordings.mkdir()
            database = root / "history.db"
            connection = create_history_database(database)
            self.addCleanup(connection.close)
            insert_history(connection, 1, "invented.wav", "invented conflict raw", None)
            write_wav(recordings / "invented.wav")
            corpus = root / "corpus"
            sample = corpus / "samples" / "1"
            sample.mkdir(parents=True)
            conflicting_audio = b"invented conflicting prior audio"
            (sample / "audio.wav").write_bytes(conflicting_audio)
            (sample / "metadata.json").write_text(
                json.dumps(
                    {
                        "raw_transcript": "invented different raw",
                        "post_processed_transcript": None,
                        "production_transcript": "invented different raw",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(collect_corpus.ConflictingOutput):
                collect_corpus.collect_once(database, recordings, corpus)
            self.assertEqual((sample / "audio.wav").read_bytes(), conflicting_audio)
            self.assertEqual({path.name for path in sample.iterdir()}, {"audio.wav", "metadata.json"})


class RunnerAndSummaryTests(unittest.TestCase):
    def test_exact_arm_interleaving_warmup_exclusion_aggregation_and_review_sheet(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corpus = create_corpus(root)
            appdir, executable = create_fake_app(root)
            output = root / "results" / "raw"
            config = root / "phase1.json"
            arms = [
                {"name": "synthetic-device-0", "model": SYNTHETIC_MODEL, "device_index": 0},
                {"name": "synthetic-device-1", "model": SYNTHETIC_MODEL, "device_index": 1},
            ]
            write_benchmark_config(config, appdir, executable, corpus, output, arms)
            state = root / "fake-state.json"
            with mock.patch.dict(os.environ, {"TASK30_FAKE_STATE": str(state)}, clear=False):
                self.assertEqual(run_benchmark.run_benchmark(config), 0)

            observations = [
                json.loads(line)
                for line in (output / "observations.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            identities = [
                (row["phase"], row["round"], row["arm"], row["sample_id"])
                for row in observations
            ]
            expected = [
                ("warmup", 1, "synthetic-device-0", "1"),
                ("warmup", 1, "synthetic-device-1", "1"),
            ]
            for round_number in range(1, 6):
                expected.extend(
                    [
                        ("timed", round_number, "synthetic-device-0", "1"),
                        ("timed", round_number, "synthetic-device-1", "1"),
                    ]
                )
            self.assertEqual(identities, expected)
            for row in observations:
                arm = next(arm for arm in arms if arm["name"] == row["arm"])
                self.assertEqual(
                    row["argv"],
                    [
                        str(executable),
                        "--transcribe-file",
                        str(corpus / "samples" / "1" / "audio.wav"),
                        "--model",
                        SYNTHETIC_MODEL,
                        "--device-index",
                        str(arm["device_index"]),
                        "--repeat",
                        "1",
                        "--json",
                    ],
                )

            report = root / "results" / "report"
            errors, summary_path, review_path = summarize_results.summarize(output, report)
            self.assertEqual(errors, [])
            summary = summary_path.read_text(encoding="utf-8")
            self.assertIn("| synthetic-device-0 | 5 | 20.000 | 6.667 | 10.000 | 30.000 |", summary)
            self.assertNotIn("2000.000", summary)
            self.assertIn("## Nondeterministic outputs (2)", summary)
            self.assertIn("| synthetic-device-0 | true |", summary)
            review_rows = list(csv.DictReader(io.StringIO(review_path.read_text(encoding="utf-8"))))
            self.assertEqual(len(review_rows), 4)
            self.assertEqual(
                {row["variant_count"] for row in review_rows}, {"2"}
            )
            for row in review_rows:
                self.assertEqual(row["production_transcript"], "invented raw 1")
                self.assertNotEqual(row["candidate_text"], "")
                self.assertEqual((row["materiality"], row["preference"], row["notes"]), ("", "", ""))

            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(report.stat().st_mode), 0o700)
            for private_file in (
                output / "run.json",
                output / "observations.jsonl",
                output / "completion.json",
                summary_path,
                review_path,
            ):
                self.assertEqual(stat.S_IMODE(private_file.stat().st_mode), 0o600)
            run_manifest = json.loads((output / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run_manifest["installed_qq_dictation_commit"], SYNTHETIC_COMMIT)
            self.assertEqual(
                run_manifest["machine_state"]["device_list"]["argv"],
                [str(executable), "--list-devices"],
            )
            self.assertIn("process_container_evidence", run_manifest["machine_state"])

    def test_failed_arm_is_durable_and_not_silently_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corpus = create_corpus(root)
            appdir, executable = create_fake_app(root)
            output = root / "raw-failure"
            config = root / "failure.json"
            arms = [
                {"name": "synthetic-failing-arm", "model": SYNTHETIC_MODEL, "device_index": 1}
            ]
            write_benchmark_config(config, appdir, executable, corpus, output, arms)
            state = root / "fake-state.json"
            with mock.patch.dict(
                os.environ,
                {"TASK30_FAKE_STATE": str(state), "TASK30_FAKE_FAIL_DEVICE": "1"},
                clear=False,
            ):
                self.assertEqual(run_benchmark.run_benchmark(config), 1)

            lines = (output / "observations.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 6)
            records = [json.loads(line) for line in lines]
            self.assertTrue(all(record["failure_mode"] == "nonzero_exit" for record in records))
            self.assertTrue(all(record["exit_status"] == 9 for record in records))
            self.assertTrue(all("synthetic arm failure" in record["stderr"] for record in records))
            completion = json.loads((output / "completion.json").read_text(encoding="utf-8"))
            self.assertEqual(completion["status"], "complete_with_failures")
            self.assertEqual((completion["observations"], completion["failures"]), (6, 6))

            report = root / "failure-report"
            errors, summary_path, review_path = summarize_results.summarize(output, report)
            self.assertEqual(errors, [])
            summary = summary_path.read_text(encoding="utf-8")
            self.assertIn("## Failures (6)", summary)
            review_rows = list(csv.DictReader(io.StringIO(review_path.read_text(encoding="utf-8"))))
            self.assertEqual(len(review_rows), 1)
            self.assertEqual(review_rows[0]["status"], "no successful timed output")
            self.assertEqual(review_rows[0]["candidate_text"], "")


if __name__ == "__main__":
    unittest.main()
