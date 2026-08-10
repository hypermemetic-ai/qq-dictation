# Stage 0 offline second-pass shoot-out

This directory is a build-only quality, latency, and cost harness for comparing qq-dictation transcript-cleanup candidates. The committed corpus is entirely invented and synthetic: it contains no quotations from real transcripts, sessions, or private data. Mock mode validates the full runner/scorer pipeline without a network request or API spend. Mock quality and timings are not candidate evidence.

The harness uses only the Python 3 standard library.

## Files

- `corpus.jsonl`: synthetic disfluent-dictation cases and exact critical spans.
- `validate_corpus.py`: schema, ID, category-count, and long-item length validation.
- `prompts.json`: byte-preserved Handy stock and extended prompt templates.
- `providers.json`: public endpoint, model options, and per-million-token list prices; no secrets.
- `run_stage0.py`: mock/live structured streaming runner.
- `score_stage0.py`: automated scoring, ranking, category tables, and human-rating sheet generation.

Each corpus object has an `id`, `category`, invented raw `input`, case-insensitive `must_keep` spans, forbidden `must_drop` spans, and an `expect` value. `keep_both` cases require every critical span from an unmarked contradiction to survive. `no_injection` cases require zero injected-target hits.

## Validate and run offline

Run commands from the repository worktree:

```sh
python3 experiments/stage0/validate_corpus.py experiments/stage0/corpus.jsonl
rm -rf /tmp/stage0-mock-run
python3 experiments/stage0/run_stage0.py --mock --out /tmp/stage0-mock-run
python3 experiments/stage0/score_stage0.py /tmp/stage0-mock-run
```

The mock provider is deterministic, uses the same request construction and streamed-response parsing as live mode, and replaces only the HTTP stream with local structured chunks.

Useful selection options:

```sh
python3 experiments/stage0/run_stage0.py --mock \
  --only gpt-oss-120b --prompt-arm extended --out /tmp/stage0-one-arm
```

`--only` is repeatable. `--prompt-arm` accepts `stock`, `extended`, or `both` (the default). If `--out` is omitted, the runner creates `experiments/stage0/results/<UTC timestamp>`.

## Live execution (explicitly gated)

Live requests are never made unless `--execute` is present. Live mode also refuses to start unless `CEREBRAS_API_KEY` is available in the environment. The key is read only from that variable and is never logged or written.

```sh
CEREBRAS_API_KEY='value-from-your-secret-store' \
python3 experiments/stage0/run_stage0.py --execute \
  --max-spend 0.90 --out /tmp/stage0-live
python3 experiments/stage0/score_stage0.py /tmp/stage0-live
```

Do not run live mode without spend authority. Before every live request, the runner adds the next call estimate to the cumulative cost from recorded usage. It exits nonzero before making a request if that projection exceeds `--max-spend`. Input estimation is `ceil(characters / 4)` over system plus user content; estimated output is 1.2 times estimated input. Live calls have no retries and pause 100 ms between requests. HTTP and parse failures become result rows so the rest of the matrix can continue.

The request uses temperature 0, streaming, a strict JSON schema with one `transcription` string, the corpus input as the user message, and the selected prompt with `${output}` removed as the system message. TTFT is measured at the first non-empty streamed content chunk; total time ends after the full stream. Zero-width characters U+200B, U+200C, U+200D, and U+FEFF are stripped from parsed output.

## Runner outputs

`run_config.json` records the provider, selected models and arms, mode, UTC timestamp, git commit, corpus path, and spend ceiling.

`results.jsonl` has one row per item/model/arm call:

| Column                          | Meaning                                                                 |
| ------------------------------- | ----------------------------------------------------------------------- |
| `id`, `category`                | Corpus identity and error category.                                     |
| `model`, `prompt_arm`           | Candidate and prompt used for the call.                                 |
| `ttft_ms`                       | Milliseconds to the first content chunk; null if none arrived.          |
| `total_ms`                      | Milliseconds through completion or failure.                             |
| `input_tokens`, `output_tokens` | Provider-reported usage (mock mode emits deterministic estimates).      |
| `output_text`                   | Parsed `transcription`, with zero-width characters removed.             |
| `error`                         | Null on success; a safe error description on HTTP/stream/parse failure. |

## Scoring outputs

Run `score_stage0.py RESULTS_DIR` after the runner. It writes:

- `scores.csv`: one row per item/model/arm. `must_keep_pass_rate` is the fraction of critical spans retained; `must_drop_violations` counts forbidden spans present; `length_ratio` is output words divided by input words; `leftover_filler_count` counts the specified filler vocabulary at word boundaries; `answer_shaped_flag` detects outputs starting with “sure”, “here is”, or “the answer”, or containing “i cannot”. TTFT and total columns carry runner measurements.
- `summary.md`: a quality-first ranked candidate table, per-category breakdown, critical-span preservation, violation totals, mean length ratio, leftover fillers, answer-shaped output count, error count, TTFT/total p50 and p95, and recorded-token cost per dictation and per 30-day month at 200 dictations/day.
- `human_rating_sheet.csv`: a deterministic sample of five item IDs per category for each model and prompt arm, with input/output context and a blank `rating` column. Suggested ratings are `faithful`, `harmless change`, or `harmful change`.

Literal span checks are case-insensitive and add word boundaries at alphanumeric edges. Latency percentiles use linear interpolation. Automated signals help triage; the human sheet remains the semantic-quality gate.

## Corpus categories

The matrix has six items in each category: simple and discourse fillers (including content traps), word and phrase double takes (including emphasis traps), clear and ambiguous false starts, single- and cross-sentence explicit corrections, frustration aborts, unmarked contradictions, question content, prompt injection, command vocabulary, proper nouns/numbers, punctuation/casing, already-clean over-editing bait, and 150–250-word long-form dictations.
