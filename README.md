# @hypermemetic-ai/qq-dictation

Private ESM package providing an in-process Cordis voice-input plugin for the core DSH host.

## Commands

The package defines one lifecycle command:

```sh
npm test
```

It checks source syntax, then runs Node's test runner. There is no package-defined start or development command. The focused offline reproduction is documented under [Transcription endpointing](#transcription-endpointing).

## Repository map

[`package.json`](package.json) defines the public entry points and is the canonical package manifest:

| Package entry | Source |
| --- | --- |
| `@hypermemetic-ai/qq-dictation` | [`src/plugin.mjs`](src/plugin.mjs) |
| `@hypermemetic-ai/qq-dictation/service` | [`src/service.mjs`](src/service.mjs) |
| `@hypermemetic-ai/qq-dictation/http` | [`src/http.mjs`](src/http.mjs) |
| `@hypermemetic-ai/qq-dictation/recognizer` | [`src/recognizer.mjs`](src/recognizer.mjs) |

The DSH bundle metadata points to [`cordis.patch.yml`](cordis.patch.yml). Package coverage lives in [`test/dictation.test.mjs`](test/dictation.test.mjs) and [`test/recognizer.test.mjs`](test/recognizer.test.mjs). [`src/service.mjs`](src/service.mjs) has the highest relative-module fan-in, so changes there warrant particular attention to its importers and the test suite.

## Change routing

- Change a public package entry at the source mapped in the table, and keep [`package.json`](package.json) aligned if the export surface changes.
- Change DSH bundle patch configuration in [`cordis.patch.yml`](cordis.patch.yml).
- Add or update package-level coverage under [`test/`](test/), then run `npm test`.

Keep source as ESM: the manifest declares `"type": "module"`. Beyond the exported entry-point mapping above, consult the linked source before assuming component behavior; the repository metadata does not establish further runtime responsibilities.

## Transcription endpointing

The production audio path is:

1. `src/client.js` captures every microphone callback, downsamples it to 16 kHz
   mono PCM16, and constructs one WAVE when the user ends dictation.
2. `src/http.mjs` extracts that WAVE from the multipart request without changing
   it.
3. `src/service.mjs` selects either the incoming WAVE or previously buffered
   chunks, calls the recognizer once, and submits the returned text once.
4. `src/recognizer.mjs` writes a temporary file and invokes
   `handy --transcribe-file <file> --json` in a fresh process.

Handy's batch-file command reports that it uses “no mic, no VAD.” This bypasses
Handy's enabled microphone VAD setting; the inspected installation uses Whisper
Large v3 Turbo. Runtime logs show the reported odd tail on a 53.25-second batch
and unspoken “Thank you.” tails on roughly 31- and 37-second batches. Those runs
used automatic language detection and no initial prompt. The integration starts
a fresh process with only file and JSON flags; it supplies no prior context,
no-speech threshold, decoding, or segmentation options. This rules out
application prompt carryover and points to silence decoded around/after
Whisper's approximately 30-second windows. Consequently, the model previously
received all leading and trailing room tone through the user's send gesture.
Whisper-family batch models can decode no-speech as plausible stock phrases
or arbitrary language, and can generate a continuation after real speech
followed by a long silent tail. The HTTP/service path contains no text
concatenation or retry that duplicates a recognized tail: it makes one
recognition call and one draft merge for a successful end request.

`src/recognizer.mjs` now restores endpoint detection at the boundary where it
was lost. For PCM16 mono input it:

- measures 20 ms RMS frames and estimates the leading and trailing stationary
  floors independently from robust endpoint probes;
- uses the louder conservative endpoint-noise floor, so an AGC-raised RMS-400
  tail cannot be hidden by an RMS-40 lead, while a strong stationary-looking
  voiced edge is not allowed to raise its own seed threshold;
- finds strict speech seeds at an absolute floor or 9 dB over endpoint noise and
  requires activity in at least 60% of a 120 ms window, so a one-sample click or
  10 ms tap cannot seed speech;
- always evaluates robust full-span modulation (not only when seed detection
  fails), preserving short connected speech instead of trimming it down to its
  central energy peaks; a quiet stable endpoint disables this recovery so a
  real silence/room-tone tail stays removable;
- after a real seed exists, expands its boundaries with lower hysteresis and
  120 ms gap tolerance, retaining quieter/unvoiced speech while stopping at a
  sustained stationary endpoint;
- skips Handy entirely when there is no supported speech; and
- trims long non-speech endpoints while preserving 200 ms of context around
  detected speech.

Seed evidence and trim-boundary evidence are deliberately separate. This avoids
two unsafe retries: treating every frame over a fixed absolute threshold as
speech (which relabels AGC-raised RMS-400 room tone), and letting a local terminal
peak lend speech evidence to the stationary tail before it. Full-span recovery
is likewise independent of the primary seed result, fixing the case where an
adaptive gate found a valid central peak island but used that island as a
severely chopped output boundary. Once sustained evidence exists, hysteresis can
safely reconnect its lower-energy neighbors without making an isolated terminal
transient into a final speech island.

This is acoustic mitigation, not transcript filtering. Handy's returned text
is not matched or rewritten, so a genuinely spoken “thank you” and unusual but
valid phrases are preserved. Unknown or unsupported WAVE encodings are passed
through unchanged rather than risking destructive sample parsing. That favors
preserving speech at the cost of not applying the mitigation to legacy
non-PCM16-mono input.

No lightweight energy detector perfectly separates every sound. In particular,
a truly constant strong voiced tone and equally strong stationary ambience are
indistinguishable from frame energy alone. The strong full-span mode deliberately
favors preserving speech in that ambiguity; unusually loud stationary noise can
therefore pass, while a very quiet perfectly stationary voice can be rejected.
Normal speech passes through robust level modulation, and the observed
RMS-400 ambience stays below the strong threshold. The 200 ms pad similarly
favors preserving final phonemes over removing every last noise frame.

Endpointing is enabled by default. Plugin configuration may set `endpointing`
to `false` for rollback, or provide overrides for `frameMs`, `minSpeechMs`,
`paddingMs`, `absoluteThresholdDb`, `noiseMarginDb`, `noisePercentile`,
`strongSpeechThresholdDb`, and `minActivityRangeDb`. Lowering the strong-speech
threshold increases sensitivity to both quiet constant voice and stationary
ambience; lowering the minimum activity range similarly relaxes stationarity
rejection.

### Files and validation

- `src/recognizer.mjs` parses PCM16 mono WAVE, rejects no-speech, trims
  endpoints, and only then invokes Handy.
- `test/recognizer.test.mjs` covers all-silent audio, low and 30-second RMS-400
  room tone, isolated clicks, one-sample/10 ms terminal transients on stationary
  noise, 120/500/1000 ms constant full-span voice, 600/800 ms connected
  full-span voice retained byte-for-byte, quiet modulated speech, moderate
  amplitude-3500 and connected peak-5000 voice, one-second and dominant
  15/30/32-second RMS-400 tails, both terminal transients after a 30-second
  noisy speech tail, RMS-400 followed by silence, 400/500/600/700 ms moderate
  utterances between a two-second RMS-40 lead and two-second RMS-400 tail, a
  longer quiet-lead/raised-tail AGC ramp, unsupported audio, and unmodified
  legitimate “thank you”/unusual text.
- `scripts/reproduce-hallucinated-tail.mjs` is a model-free reproduction whose
  fake batch decoder emits generated text when zero or raised-room-tone tails
  (including a moderate-amplitude utterance and click/tap transients) reach a
  long batch window.
- `package.json` runs syntax checks for every source module and exposes the
  focused reproduction command.
- `README.md` records the traced runtime path, root-cause evidence, mitigation,
  configuration, and energy-only tradeoffs.

Run the complete package validation and focused reproduction with:

```sh
npm test
npm run reproduce:tail
```

Validation performed for this change: `npm test` passed all 16 tests; the focused
reproduction passed all silence, room-tone, transient-tail, and full-span-speech
scenarios; `git diff --check` passed; and `node --check` passed for every source,
test, and reproduction JavaScript file.

The fake batch decoder examines the actual temporary WAVE handed to Handy. It
simulates generated output after a long zero or RMS-400 tail and on a no-speech
file, including adversarial terminal transients, without requiring a model
download or matching any production transcript.
