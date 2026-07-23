# Local distribution

`qq-dictation` is a private, per-user Handy distribution for QQ's Linux
workstation. It keeps Parakeet as the fast speech recognizer and adds a tiny
resident classifier that removes contextual speech disfluencies.

## Reproducible inputs

- Handy upstream baseline:
  `8a362e9eba59d4057fda79b7f38f5b0d5cbabf65`
- FDT Mini revision:
  `677a8a0c20f23858e3c581977111a572999ee487`
- Rust: `1.96.0`
- Bun: `1.3.3`
- Build image: Ubuntu `24.04`

The model fetch verifies all three artifact hashes. Model weights live below
`~/.local/share/com.pais.handy/text-cleanup/fdt-mini-11m` and are never stored
in Git or bundled into the application. The upstream model metadata says
Apache-2.0, but its training description includes DailyDialog data licensed
CC BY-NC-SA 4.0; this distribution is intentionally private and local.

## Build and install

```bash
scripts/build-local.sh
scripts/install-local.sh
```

The build runs in a pinned Docker environment because the host intentionally
does not carry the full GTK/WebKit development stack. Cargo and bundle output
are cached in ignored `.docker-cache/` and `.local-build/` directories. The
ONNX Runtime download cache is persisted alongside Cargo's cache so a fresh
container can always relink the application and its tests. The builder refuses
a dirty source tree and writes the exact Git commit into the AppDir as
`qq-dictation-commit`.

Installation writes only to the current user's directories:

- application: `~/.local/opt/qq-dictation/Handy.AppDir`
- launcher and PTT bridge: `~/.local/bin`
- user service: `~/.config/systemd/user/handy-ptt.service`
- model: Handy's existing app-data directory

Existing Handy app data—settings, ASR models, history, and logs—is not replaced.
The installer backs up any launcher, bridge, service, settings file, or prior
qq-dictation AppDir that it supersedes. It changes two distribution-owned
settings: the overlay is set to `minimal`, and upstream update checks are
disabled so a future stock Handy release cannot replace the tracked local
build. Updates to qq-dictation are built and installed explicitly from Git.

## Runtime policy

For English output, FDT runs before custom-word correction and replaces Handy's
unconditional filler filter. It uses 128-token windows with 32-token overlap,
first-WordPiece predictions, and maximum-context selection for words seen in
more than one window. A deletion run and its adjacent comma/capital repair form
one transaction. The transaction confidence is the geometric mean of its
action probabilities; the default acceptance threshold is `0.70`.

Override the threshold for calibration without rebuilding:

```bash
HANDY_FDT_MIN_SPAN_CONFIDENCE=0.75 handy --start-hidden
```

Any missing or changed artifact, inference error, invalid output shape,
uncovered word, runtime panic, or non-English output goes through Handy's
unchanged legacy cleanup path. The FDT session stays resident independently of
the ASR unload policy.

## Recording indicator

The installer sets Handy's existing native overlay to `minimal`. Recording
therefore produces a small bottom-center card above Ghostty/herdr/Pi or Codex;
the behavior belongs to Handy itself, not to a particular terminal harness.
