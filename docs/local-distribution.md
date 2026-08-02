# Local distribution

`qq-dictation` is a private, per-user Handy distribution for QQ's Linux
workstation. It preserves Handy's local speech recognition while adding
Herdr-pane target binding, a Right-Control push-to-talk bridge, and a
reproducible user-local installation.

## Reproducible inputs

- Handy upstream baseline:
  `8a362e9eba59d4057fda79b7f38f5b0d5cbabf65`
- Rust: `1.96.0`
- Bun: `1.3.3`
- Build image: Ubuntu `24.04`

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
- models and history: Handy's existing app-data and Hugging Face cache

Existing Handy app data—settings, ASR models, history, and logs—is not replaced.
The installer backs up any launcher, bridge, service, settings file, or prior
qq-dictation AppDir that it supersedes. It enables Herdr target binding and
push-to-talk, disables API post-processing and the recording overlay, and
disables upstream update checks so a stock release cannot replace the tracked
local build. Updates to qq-dictation are built and installed explicitly from
Git.

## Runtime policy

The selected Handy ASR model is the only model pass. qq-dictation does not
fetch or run a transcript-cleanup classifier, and API post-processing is
disabled by installation policy. Handy's built-in deterministic custom-word
and filler handling remains part of its normal transcription path.

When recording begins in Herdr on X11, qq-dictation captures the focused pane
and delivers the finished transcript directly to that pane even if focus moves.
Non-Herdr recordings retain Handy's focus-based insertion behavior. Identified
Herdr capture or delivery failures fail closed rather than typing into whichever
application happens to be focused.

The Right-Control bridge sends separate realtime signals for PTT press
(`SIGRTMIN`) and release (`SIGRTMIN+1`). It does not use Handy's toggle signal:
an ignored press while a previous transcript is still processing therefore
cannot invert the next release into a recording start.

## Recording state

The installer disables Handy's recording overlay. On this Cinnamon/X11 host,
GTK layer-shell is unavailable and the overlay otherwise becomes a normal
bottom-of-screen window that raises the desktop panel. Handy's tray icon still
reports idle, recording, and transcribing state. The Right-Control bridge draws
its own small X11 recording badge as an override-redirect window, so recording
remains visible without creating a taskbar entry or taking focus.

The installer also enables auto-submit. Herdr-bound transcripts are followed by
Enter in their captured pane after successful delivery.
