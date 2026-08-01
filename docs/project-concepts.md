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
