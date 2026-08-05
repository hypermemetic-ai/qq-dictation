# qq-dictation project concepts

These terms extend the shared QQ vocabulary in `CONCEPTS.md`.

- **qq-dictation**: QQ's private, Linux-focused local distribution of Handy. It
  retains Handy's on-device ASR while adding Herdr pane targeting and a
  reproducible installation around QQ's Right-Control push-to-talk workflow.
- **Upstream baseline**: Handy commit
  `8a362e9eba59d4057fda79b7f38f5b0d5cbabf65`. The repository has a read-only
  conceptual relationship with `https://github.com/cjpais/Handy.git` through
  the `upstream` remote. This exact post-v0.9.4 baseline was retained because
  it includes recent Linux overlay, clipboard, and audio fixes.
- **Herdr target binding**: A per-recording association with the Herdr pane
  focused at recording start. Delivery uses Herdr's pane API rather than
  simulated keyboard focus, so later focus changes cannot redirect the text.
- **Legacy delivery**: Handy's focus-based text insertion, retained for
  recordings that genuinely start outside Herdr or when Herdr binding is
  explicitly disabled.
- **Local package**: The AppDir, push-to-talk bridge, service, and settings
  migration installed only for the current Linux user. Handy's ASR models and
  history remain in their standard shared user-data locations.
- **Dictation pair corpus**: The existing `transcription_history` table in
  `${XDG_DATA_HOME:-$HOME/.local/share}/com.pais.handy/history.db` is the sole
  source of truth for the latest 1,000 successful raw-to-second-pass text pairs;
  it stores each pair's timestamp, exact prompt, and provider-qualified model
  (reported as unknown when the row predates model capture). WAV retention is
  independent and defaults to the latest five recordings under the live
  PreserveLimit policy. `/dictation-review` accepts no arguments. It censuses all
  qualifying retained pairs in place through a read-only local process, exposing
  only aggregate census observations and up to 30 distinct close-review pairs: 12
  stratified-random, 10 diverse risk-flagged, and 8 latest consecutive pairs from
  the current exact prompt/model cohort. Only those selected pairs may enter the
  current Pi session and its current model provider; the workflow creates no corpus
  export or persisted transcript artifact and sends rows nowhere else. A text-only
  row can evaluate cleanup behavior but cannot prove ASR fidelity after its WAV is
  deleted, mechanical census signals are triage rather than quality proof, and
  subjective writing quality remains agent/operator judgment from cited evidence.
