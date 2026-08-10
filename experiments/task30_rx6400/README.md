# TASK-30 RX 6400 measurement tooling

These Python-standard-library scripts collect a private natural-dictation corpus, run the explicitly named installed qq-dictation AppDir, and produce timing and differential-review surfaces. They do not change Handy settings, retention, models, or product source.

## Private-data boundary

**The corpus, raw runs, summaries, review sheets, and watcher logs contain private audio or transcripts. They never belong in Git.** Keep every real path outside the worktree, use a private parent directory, and do not paste their contents into issues or test fixtures. Only these scripts and placeholder instructions are committed. The tests create invented WAV/database data under temporary directories and never inspect the live corpus.

The collector enforces mode `0700` on corpus/sample directories and `0600` on corpus files. The runner and summarizer create result/report directories as `0700` and files as `0600`. `seed-existing.json` is owner-maintained: the collector reads it for cohort accounting and never rewrites or extends it. Its supported shape is:

```json
{
  "cohort": "existing-saved",
  "history_ids": [101, 102, 103, 104, 105]
}
```

`sample_ids` or `ids` may replace `history_ids`. IDs in that file stay in `existing-saved`; every other collected sample is fresh. Each published `samples/<history-id>/metadata.json` has `raw_transcript`, nullable `post_processed_transcript`, and `production_transcript`. Production text is the post-processed text whenever it is non-null, otherwise the raw text.

## 1. Seed once, then watch

Substitute private absolute paths. One-shot mode copies every currently completed row with a retained regular WAV and is safe to repeat:

```sh
umask 077
python3 experiments/task30_rx6400/collect_corpus.py \
  --database '<HANDY_DATA>/history.db' \
  --recordings '<HANDY_DATA>/recordings' \
  --corpus '<PRIVATE_TASK30>/corpus'
```

Run watch mode in a dedicated terminal while collecting fresh natural dictations:

```sh
umask 077
python3 experiments/task30_rx6400/collect_corpus.py \
  --database '<HANDY_DATA>/history.db' \
  --recordings '<HANDY_DATA>/recordings' \
  --corpus '<PRIVATE_TASK30>/corpus' \
  --watch --interval 1
```

Every poll reports `existing-saved`, `fresh`, newly published, already present, and missing seeded counts without printing transcripts. Stop the watcher with **Ctrl-C** in that terminal before moving or retiring its private corpus. The SQLite connection is read-only. A sample appears only after its WAV and metadata have both been staged and the source WAV/history row have been rechecked; changed sources and conflicting prior output are refused.

## 2. Configure phase 1 (current model, devices 0/1/2)

Create a private JSON file outside Git. The executable must be inside the supplied installed `.AppDir`; the runner reads that AppDir's `qq-dictation-commit` and refuses an AppDir inside this worktree.

```json
{
  "installed_appdir": "<INSTALLED>/Handy.AppDir",
  "executable": "<INSTALLED>/Handy.AppDir/AppRun",
  "corpus": "<PRIVATE_TASK30>/corpus",
  "output_dir": "<PRIVATE_TASK30>/results/phase1-raw",
  "cohort": "existing-saved",
  "warmup_rounds": 1,
  "timed_rounds": 5,
  "timeout_seconds": 600,
  "arms": [
    {
      "name": "rx6400-device-0",
      "model": "handy-computer/parakeet-unified-en-0.6b-gguf/parakeet-unified-en-0.6b-Q8_0.gguf",
      "device_index": 0
    },
    {
      "name": "radeon-780m-device-1",
      "model": "handy-computer/parakeet-unified-en-0.6b-gguf/parakeet-unified-en-0.6b-Q8_0.gguf",
      "device_index": 1
    },
    {
      "name": "cpu-device-2",
      "model": "handy-computer/parakeet-unified-en-0.6b-gguf/parakeet-unified-en-0.6b-Q8_0.gguf",
      "device_index": 2
    }
  ]
}
```

## 3. Configure candidate runs (device 0, full corpus)

Use a new private output directory and `"cohort": "full"`. Keep listed-arm order fixed. This example covers the required installed Whisper candidate and a 1.1B Parakeet candidate; only list models that the owner has already downloaded. The runner never downloads one.

```json
{
  "installed_appdir": "<INSTALLED>/Handy.AppDir",
  "executable": "<INSTALLED>/Handy.AppDir/AppRun",
  "corpus": "<PRIVATE_TASK30>/corpus",
  "output_dir": "<PRIVATE_TASK30>/results/candidates-raw",
  "cohort": "full",
  "warmup_rounds": 1,
  "timed_rounds": 5,
  "timeout_seconds": 600,
  "arms": [
    {
      "name": "whisper-large-v3-turbo-q4",
      "model": "handy-computer/whisper-large-v3-turbo-gguf/whisper-large-v3-turbo-Q4_K_M.gguf",
      "device_index": 0
    },
    {
      "name": "parakeet-tdt-1.1b-q5",
      "model": "handy-computer/parakeet-tdt-1.1b-gguf/parakeet-tdt-1.1b-Q5_K_M.gguf",
      "device_index": 0
    }
  ]
}
```

## 4. Establish idle state, run, and summarize

**Immediately before every benchmark, the owner must freshly establish that the machine is idle and that no Docker/container or other build is active.** Do not rely on an earlier observation. Quiesce other work first, then run promptly. The runner records `/proc/loadavg`, uptime, CPU count, the installed commit, the installed executable's `--list-devices` command/output, and a `/proc` process/container scan at run start. That evidence supports—not replaces—the owner's idle-state adjudication.

Run one config from the worktree root:

```sh
umask 077
python3 experiments/task30_rx6400/run_benchmark.py '<PRIVATE_TASK30>/phase1.json'
```

For each sample, every warm-up round and then every timed round invokes arms in listed order. Every observation is a distinct process with only:

```text
<executable> --transcribe-file <wav> --model <exact-id> --device-index <exact-index> --repeat 1 --json
```

`observations.jsonl` is fsynced after each private record. Exact argv, UTC, phase/round, exit status, parsed and complete stdout, and complete stderr are retained. Failed invocations remain in place, the remaining matrix runs, and final exit is nonzero after `completion.json` is durable.

Summarize into another new private directory:

```sh
python3 experiments/task30_rx6400/summarize_results.py \
  '<PRIVATE_TASK30>/results/phase1-raw' \
  --output '<PRIVATE_TASK30>/results/phase1-report'
```

`summary.md` validates schedule/argv and reports per-arm and per-arm/file best and median real-time multiple plus cold-load milliseconds from **timed observations only**, failures, observed load-log `supports_streaming`, and nondeterministic variant counts. `differential-review.csv` places production and every distinct timed candidate text side by side with blank `materiality`, `preference`, and `notes` columns. It is a differential review aid, not an absolute WER or semantic-quality claim. Fill those columns only during the owner's adjudication session; summarization refuses to overwrite an existing report directory.
