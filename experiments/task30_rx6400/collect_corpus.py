#!/usr/bin/env python3
"""Snapshot Handy history rows and WAV files into a private TASK-30 corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Sequence

from task30_common import (
    Task30Error,
    ensure_private_directory,
    list_corpus_sample_ids,
    load_seed_ids,
    private_write_json,
    require_absolute,
    require_plain_directory,
    require_plain_file,
)


class CollectionError(Task30Error):
    pass


class SourceChanged(CollectionError):
    pass


class ConflictingOutput(CollectionError):
    pass


@dataclass(frozen=True)
class HistoryRow:
    history_id: int
    file_name: str
    timestamp: int
    raw_transcript: str
    post_processed_transcript: str | None

    @property
    def sample_id(self) -> str:
        return str(self.history_id)

    def metadata(self) -> dict[str, object]:
        production = (
            self.post_processed_transcript
            if self.post_processed_transcript is not None
            else self.raw_transcript
        )
        return {
            "post_processed_transcript": self.post_processed_transcript,
            "production_transcript": production,
            "raw_transcript": self.raw_transcript,
        }


@dataclass(frozen=True)
class CollectionReport:
    newly_published: int
    already_present: int
    existing_saved: int
    fresh: int
    seed_missing: int


def _read_only_connection(database: Path) -> sqlite3.Connection:
    require_plain_file(database, "history database")
    uri = database.resolve().as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection
    except sqlite3.Error as error:
        raise CollectionError(f"cannot open history database read-only: {error}") from error


def _map_row(row: sqlite3.Row) -> HistoryRow:
    history_id = row["id"]
    file_name = row["file_name"]
    timestamp = row["timestamp"]
    raw = row["transcription_text"]
    processed = row["post_processed_text"]
    if (
        isinstance(history_id, bool)
        or not isinstance(history_id, int)
        or history_id <= 0
        or not isinstance(file_name, str)
        or isinstance(timestamp, bool)
        or not isinstance(timestamp, int)
        or not isinstance(raw, str)
        or (processed is not None and not isinstance(processed, str))
    ):
        safe_id = history_id if isinstance(history_id, int) else "unknown"
        raise CollectionError(f"history row {safe_id} has an invalid schema")
    return HistoryRow(history_id, file_name, timestamp, raw, processed)


def read_completed_rows(database: Path) -> list[HistoryRow]:
    connection = _read_only_connection(database)
    try:
        rows = connection.execute(
            "SELECT id, file_name, timestamp, transcription_text, post_processed_text "
            "FROM transcription_history "
            "WHERE audio_available = 1 AND transcription_text != '' "
            "ORDER BY id"
        ).fetchall()
        return [_map_row(row) for row in rows]
    except sqlite3.Error as error:
        raise CollectionError(f"cannot query completed history rows: {error}") from error
    finally:
        connection.close()


def read_row(database: Path, history_id: int) -> HistoryRow | None:
    connection = _read_only_connection(database)
    try:
        row = connection.execute(
            "SELECT id, file_name, timestamp, transcription_text, post_processed_text "
            "FROM transcription_history "
            "WHERE id = ? AND audio_available = 1 AND transcription_text != ''",
            (history_id,),
        ).fetchone()
        return None if row is None else _map_row(row)
    except sqlite3.Error as error:
        raise CollectionError(f"cannot recheck history row {history_id}: {error}") from error
    finally:
        connection.close()


def _safe_wav_basename(file_name: str) -> bool:
    if not file_name or file_name in {".", ".."}:
        return False
    if Path(file_name).name != file_name or Path(file_name).suffix != ".wav":
        return False
    return not any(ord(character) < 32 or ord(character) == 127 for character in file_name)


def _signature(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _stream_copy(source: BinaryIO, destination: BinaryIO) -> None:
    shutil.copyfileobj(source, destination, length=1024 * 1024)


def _copy_stable_regular(source_path: Path, destination_path: Path) -> tuple[int, int, int, int, int, int]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        source_descriptor = os.open(source_path, flags)
    except OSError as error:
        raise SourceChanged(f"sample source is unavailable or unsafe: {source_path}") from error

    try:
        before = os.fstat(source_descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise SourceChanged(f"sample source is not a regular file: {source_path}")
        destination_descriptor = os.open(
            destination_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        try:
            os.fchmod(destination_descriptor, 0o600)
            with os.fdopen(os.dup(source_descriptor), "rb", closefd=True) as source_file:
                with os.fdopen(destination_descriptor, "wb", closefd=False) as destination_file:
                    _stream_copy(source_file, destination_file)
                    destination_file.flush()
            os.fsync(destination_descriptor)
        finally:
            os.close(destination_descriptor)
        after = os.fstat(source_descriptor)
    finally:
        os.close(source_descriptor)

    if _signature(before) != _signature(after) or destination_path.stat().st_size != before.st_size:
        raise SourceChanged(f"sample source changed while copying: {source_path}")
    try:
        path_after = source_path.lstat()
    except FileNotFoundError as error:
        raise SourceChanged(f"sample source disappeared after copying: {source_path}") from error
    if _signature(path_after) != _signature(after):
        raise SourceChanged(f"sample source was replaced while copying: {source_path}")
    return _signature(after)


def _hash_stable_regular(path: Path, label: str) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise SourceChanged(f"{label} is unavailable or unsafe: {path}") from error
    digest = hashlib.sha256()
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise SourceChanged(f"{label} is not a regular file: {path}")
        with os.fdopen(os.dup(descriptor), "rb", closefd=True) as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if _signature(before) != _signature(after):
        raise SourceChanged(f"{label} changed while verifying: {path}")
    return digest.digest()


def _read_metadata(path: Path, sample_id: str) -> dict[str, object]:
    require_plain_file(path, f"sample {sample_id} metadata")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ConflictingOutput(f"sample {sample_id} has unreadable metadata") from error
    if not isinstance(value, dict):
        raise ConflictingOutput(f"sample {sample_id} metadata is not an object")
    return value


def _validate_existing(row: HistoryRow, source_path: Path, sample_path: Path, database: Path) -> None:
    try:
        require_plain_directory(sample_path, f"sample {row.sample_id}")
    except Task30Error as error:
        raise ConflictingOutput(str(error)) from error
    names = {entry.name for entry in sample_path.iterdir()}
    if names != {"audio.wav", "metadata.json"}:
        raise ConflictingOutput(f"sample {row.sample_id} has conflicting prior output")

    metadata_path = sample_path / "metadata.json"
    audio_path = sample_path / "audio.wav"
    existing_metadata = _read_metadata(metadata_path, row.sample_id)
    expected = row.metadata()
    if any(existing_metadata.get(key) != value for key, value in expected.items()):
        raise ConflictingOutput(f"sample {row.sample_id} metadata conflicts with live history")
    try:
        require_plain_file(audio_path, f"sample {row.sample_id} audio")
    except Task30Error as error:
        raise ConflictingOutput(str(error)) from error
    if _hash_stable_regular(source_path, "live WAV") != _hash_stable_regular(
        audio_path, "corpus WAV"
    ):
        raise ConflictingOutput(f"sample {row.sample_id} audio conflicts with live recording")
    if read_row(database, row.history_id) != row:
        raise SourceChanged(f"history row {row.sample_id} changed while verifying")

    sample_path.chmod(0o700)
    audio_path.chmod(0o600)
    metadata_path.chmod(0o600)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_row(row: HistoryRow, database: Path, recordings: Path, samples: Path) -> bool:
    if not _safe_wav_basename(row.file_name):
        raise CollectionError(f"history row {row.sample_id} has an unsafe WAV basename")
    source_path = recordings / row.file_name
    sample_path = samples / row.sample_id
    if sample_path.exists() or sample_path.is_symlink():
        _validate_existing(row, source_path, sample_path, database)
        return False

    staging = Path(tempfile.mkdtemp(prefix=f".{row.sample_id}.tmp-", dir=samples))
    staging.chmod(0o700)
    try:
        source_signature = _copy_stable_regular(source_path, staging / "audio.wav")
        private_write_json(staging / "metadata.json", row.metadata())
        current_row = read_row(database, row.history_id)
        if current_row != row:
            raise SourceChanged(f"history row {row.sample_id} changed while copying")
        try:
            current_source = source_path.lstat()
        except FileNotFoundError as error:
            raise SourceChanged(f"sample source disappeared before publication: {source_path}") from error
        if _signature(current_source) != source_signature:
            raise SourceChanged(f"sample source changed before publication: {source_path}")
        if sample_path.exists() or sample_path.is_symlink():
            raise ConflictingOutput(f"sample {row.sample_id} appeared during publication")
        os.rename(staging, sample_path)
        _fsync_directory(samples)
        return True
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _secure_complete_corpus_samples(corpus: Path) -> list[str]:
    sample_ids = list_corpus_sample_ids(corpus)
    for sample_id in sample_ids:
        sample = corpus / "samples" / sample_id
        names = {entry.name for entry in sample.iterdir()}
        if names != {"audio.wav", "metadata.json"}:
            raise ConflictingOutput(f"sample {sample_id} has conflicting prior output")
        audio = sample / "audio.wav"
        metadata = sample / "metadata.json"
        require_plain_file(audio, f"sample {sample_id} audio")
        require_plain_file(metadata, f"sample {sample_id} metadata")
        sample.chmod(0o700)
        audio.chmod(0o600)
        metadata.chmod(0o600)
    return sample_ids


def collect_once(database: Path, recordings: Path, corpus: Path) -> CollectionReport:
    require_plain_directory(recordings, "recordings directory")
    ensure_private_directory(corpus)
    samples = corpus / "samples"
    ensure_private_directory(samples)

    seed_manifest = corpus / "seed-existing.json"
    if seed_manifest.exists() or seed_manifest.is_symlink():
        require_plain_file(seed_manifest, "seed-existing.json")
        seed_manifest.chmod(0o600)
    seed_ids = load_seed_ids(corpus)

    newly_published = 0
    already_present = 0
    for row in read_completed_rows(database):
        if _publish_row(row, database, recordings, samples):
            newly_published += 1
        else:
            already_present += 1

    all_ids = set(_secure_complete_corpus_samples(corpus))
    existing_saved = len(all_ids.intersection(seed_ids))
    fresh = len(all_ids.difference(seed_ids))
    return CollectionReport(
        newly_published=newly_published,
        already_present=already_present,
        existing_saved=existing_saved,
        fresh=fresh,
        seed_missing=len(seed_ids.difference(all_ids)),
    )


def _print_report(report: CollectionReport) -> None:
    print(
        "collection: "
        f"existing-saved={report.existing_saved} fresh={report.fresh} "
        f"newly-published={report.newly_published} already-present={report.already_present} "
        f"seed-missing={report.seed_missing}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Copy completed Handy history/WAV pairs into a private TASK-30 corpus."
    )
    parser.add_argument("--database", required=True, help="absolute path to Handy history.db")
    parser.add_argument(
        "--recordings", required=True, help="absolute path to Handy's recordings directory"
    )
    parser.add_argument("--corpus", required=True, help="absolute private corpus directory")
    parser.add_argument("--watch", action="store_true", help="poll until interrupted")
    parser.add_argument(
        "--interval", type=float, default=1.0, help="watch poll interval in seconds (default: 1)"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        database = require_absolute(args.database, "database")
        recordings = require_absolute(args.recordings, "recordings")
        corpus = require_absolute(args.corpus, "corpus")
        if args.interval <= 0:
            raise CollectionError("interval must be greater than zero")
        if not args.watch:
            _print_report(collect_once(database, recordings, corpus))
            return 0

        while True:
            try:
                _print_report(collect_once(database, recordings, corpus))
            except Task30Error as error:
                print(f"collection poll failed: {error}", file=sys.stderr)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("collector stopped", file=sys.stderr)
        return 0
    except (Task30Error, OSError) as error:
        print(f"collection failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
