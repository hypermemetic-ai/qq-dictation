# Local distribution

`qq-dictation` is a private, per-user Handy distribution for QQ's Linux
workstation. It preserves Handy's local speech recognition while adding
Herdr-pane target binding, a Right-Control armed dictation-mode bridge, and a
reproducible user-local installation.

## Reproducible inputs

- Handy upstream baseline:
  `8a362e9eba59d4057fda79b7f38f5b0d5cbabf65`
- Rust: `1.96.0`
- Bun: `1.3.3`
- Build image: Ubuntu `24.04`

## Build and install

```bash
QQ_BUILD_MEM=8g scripts/build-local.sh
scripts/install-local.sh
```

Read [`../BUILD-LESSONS.md`](../BUILD-LESSONS.md) before building. The 8 GiB
allowance is the current proven value for this machine's rebuilt toolchain
image; the older 5 GiB default was later OOM-killed by ggml-vulkan compilation.
It remains contained with no additional container swap, two CPUs, and one
Cargo/CMake job.

The build runs in a pinned Docker environment because the host intentionally
does not carry the full GTK/WebKit development stack. Cargo and bundle output
are cached in ignored `.docker-cache/` and `.local-build/` directories. The
ONNX Runtime download cache is persisted alongside Cargo's cache so a fresh
container can always relink the application and its tests. The builder refuses
a dirty source tree and writes the exact Git commit into the AppDir as
`qq-dictation-commit`.

Installation writes only to the current user's directories:

- application: `~/.local/opt/qq-dictation/Handy.AppDir`
- launcher and dictation-mode bridge: `~/.local/bin`
- user service: `~/.config/systemd/user/handy-ptt.service`
- models and history: Handy's existing app-data and Hugging Face cache

Existing Handy app data—settings, ASR models, history, and logs—is not replaced.
The installer backs up any launcher, bridge, service, settings file, or prior
qq-dictation AppDir that it supersedes. On a fresh profile it creates the minimal
settings store before applying the same policy. It enables Herdr target binding
and push-to-talk, disables API post-processing, enables the native minimal
overlay, and disables upstream update checks so a stock release cannot replace
the tracked local build. Updates to qq-dictation are built and installed
explicitly from Git.

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

The Right-Control bridge implements a visible system-wide dictation mode.
Right-Control arms the mode and exits it; while armed, each distinct plain Space
press starts or stops a recording and each plain Delete press cancels the
active recording or in-flight transcription without delivery, staying armed.
Space/Delete used with Ctrl, Alt, or Super retain their existing behavior. The
bridge uses an explicit two-phase realtime contract: prepare (`SIGRTMIN+2`),
mode-on (`SIGRTMIN+3`), mode-off (`SIGRTMIN+4`), Space (`SIGRTMIN+5`), and
Delete (`SIGRTMIN+6`). The legacy push-to-talk pair remains on `SIGRTMIN` and
`SIGRTMIN+1`. The bridge grabs plain Space/Delete across Caps Lock and Num Lock
states without disturbing existing modified-key shortcuts. The armed overlay
appears only after both bridge and app commit the mode. The bridge never uses
Handy's toggle signal, so an
ignored press while a previous transcript is still processing cannot invert a
later action into a recording start. If the Handy process disappears or is
replaced while the mode is armed, the bridge resets to mode off, releases the
Space and Delete grabs, and the
next explicit arm re-establishes the mode with the current Handy process;
bridge or Handy restarts therefore always return to mode off.

## Recording state

The installer enables Handy's native minimal overlay for recording and
transcribing state. On this Cinnamon/X11 host, GTK layer-shell is installed but
unsupported because layer-shell is a Wayland protocol; Handy therefore uses its
transparent Tauri window with a GTK notification-window hint. Cinnamon can keep
that non-focusable, always-on-top overlay above fullscreen applications without
treating it as a normal application window and raising the desktop panel. While
the mode is armed and no work is active, the same overlay window shows a
persistent armed legend (Space starts/stops, Delete cancels, Right-Control
exits); recording and working states replace it until the work settles. The
Right-Control bridge does not draw a second indicator.

The installer also enables auto-submit. Herdr-bound transcripts are followed by
Enter in their captured pane after successful delivery.
