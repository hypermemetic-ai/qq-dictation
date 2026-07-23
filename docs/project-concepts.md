# qq-dictation project concepts

These terms extend the shared QQ vocabulary in `CONCEPTS.md`.

- **qq-dictation**: QQ's private, Linux-focused local distribution of Handy. It
  retains Handy's fast on-device ASR while adding conservative transcript
  cleanup and a reproducible installation around QQ's existing push-to-talk
  workflow.
- **Upstream baseline**: Handy commit
  `8a362e9eba59d4057fda79b7f38f5b0d5cbabf65`. The repository has a read-only
  conceptual relationship with `https://github.com/cjpais/Handy.git` through
  the `upstream` remote. This exact post-v0.9.4 baseline was retained because
  it includes recent Linux overlay, clipboard, and audio fixes.
- **FDT cleaner**: The resident `stillerman/fdt-disfluency-mini-11m`
  token-classification stage that runs after English speech recognition and
  before custom-word correction.
- **Edit transaction**: A classifier-proposed deletion run together with its
  structurally adjacent comma-removal and capitalization edits. A transaction
  is accepted or rejected as a unit so the cleaner cannot leave half an edit.
- **Maximum-context window**: One of the overlapping 128-token classifier
  windows. When a word appears in more than one window, its logits come from
  the occurrence furthest from a non-special window edge.
- **Legacy cleanup path**: Handy's existing rule-based filler-word filter. It
  remains the fail-open fallback when the FDT cleaner is unavailable or the
  effective transcription language is not English.
- **Local package**: The AppDir, model assets, push-to-talk bridge, service,
  and settings migration installed only for the current Linux user. Model
  weights are pinned and verified during installation but are never committed
  to Git.
