# qq-dictation project concepts

These terms extend the shared QQ vocabulary in `CONCEPTS.md`.

- **qq-dictation**: QQ's private, Linux-focused local distribution of Handy. It
  retains Handy's on-device ASR while adding Herdr pane targeting and a
  reproducible installation around QQ's Right-Control armed dictation-mode
  workflow (Right-Control arms/exits; Space starts/stops; Delete cancels).
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
- **Local package**: The AppDir, dictation-mode bridge, service, and settings
  migration installed only for the current Linux user. Handy's ASR models and
  history remain in their standard shared user-data locations.
- **Dictation pair corpus**: The existing `transcription_history` table in
  `${XDG_DATA_HOME:-$HOME/.local/share}/com.pais.handy/history.db` is the sole
  source of truth for the latest 1,000 successful raw-to-second-pass text pairs;
  it stores each pair's timestamp, exact prompt, and provider-qualified model
  (reported as unknown when the row predates model capture). WAV retention is
  independent and defaults to the latest five recordings under the live
  PreserveLimit policy. `/dictation-review` accepts no arguments, and a valid
  invocation authorizes immediate execution without a confirmation round. It
  materializes every qualifying pair visible in one short-lived, read-only SQLite
  snapshot, closes the database connection, and computes all census observations,
  cohort definitions, sampling, and shortfalls from that fixed result set. Handy
  may keep writing concurrently; later commits remain outside that run rather than
  causing a restart or count-drift failure. The command closely reviews up to 30
  distinct snapshot pairs: 12 stratified-random, 10 diverse risk-flagged, and 8
  latest consecutive pairs from the current exact prompt/model cohort. Sampling
  keeps the close review manageable rather than enforcing a confidentiality
  boundary. The command also mines the complete snapshot for custom-words
  dictionary candidates: distinctive terms appearing in at least 3 distinct
  rows, each reported with frequency, benefit evidence from raw-text variants,
  and a measured false-positive count from a simulation of Handy's fuzzy
  custom-word matcher at the live threshold; measured risk flags but never
  excludes a candidate, and the live settings store is read, never written.
  The command leaves Handy's database and application state unchanged
  and does not review audio. Mechanical census signals are triage rather than
  quality proof, text pairs alone cannot prove ASR fidelity, and subjective writing
  quality remains agent/operator judgment from cited evidence.
