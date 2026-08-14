# qq-dictation

`qq-dictation` is the private `qqp-dev/qq-dictation` Linux/X11 product for qq
workstations and laptops. It retains Handy's local speech recognition and adds
Herdr-pane target binding, the Left-Control q mode bridge, the adopted
second-pass cleanup prompt, and a pinned per-user build and installation path.
There is no public support, release, or self-update channel.

## Runtime behavior

- Speech recognition, VAD, audio capture, models, and history remain local.
- A legacy workstation recording started in Herdr is delivered to the pane
  focused at recording start; recordings started elsewhere use focused X11
  insertion.
- Left-Control arms or exits q mode. While armed, Space starts or stops recording
  and Delete cancels. The installed bridge and realtime signals remain the
  compatibility path.
- The A-25 remote Linux/X11 laptop path uses the workstation's serialized
  transcription, selected model/language, history, and delivery
  policy.
- The installer refreshes the adopted cleanup prompt from product source while
  preserving provider/model/API configuration, unrelated prompts, an explicit
  and the operator's overlay, Herdr, push-to-talk, and auto-submit choices.

See the [operations and build guide](docs/operations.md) for the complete build,
cache, install, rollback, second-pass, and selective-upstream contracts. The
focused [remote laptop guide](docs/remote-laptop-dictation.md) and
[wire protocol](docs/remote-dictation-protocol.md) describe the A-25 path.

## Build and update

Build and install only an exact clean Repository commit:

```bash
QQ_BUILD_MEM=8g ops/build/build-local.sh
ops/install/install-local.sh
```

The build uses the pinned Ubuntu 24.04, Rust 1.96.0, and Bun 1.3.3 environment
with the proven resource limits. Installation is per-user under
`~/.local/opt/qq-dictation`; it preserves app data and backs up replaced local
files. The local installer is operator-visible and is not part of source-only
Checks.

## Repository Check

After committing, prove the exact commit in a fresh clone:

```bash
tools/check.sh "$(git rev-parse HEAD)"
```

The runner performs the full frontend, formatting, Python, and Rust Checks and
emits one receipt bound to the exact commit and tree. Its full log is private
under the user's qq Check state directory.

## Upstream provenance

This product derives from [Handy](https://github.com/cjpais/Handy) under the
terms in [LICENSE](LICENSE). The retained upstream baseline is:

```text
8a362e9eba59d4057fda79b7f38f5b0d5cbabf65
```

Later upstream work is fetched and inspected after that baseline, selected only
when compatible with the private Linux/X11 and A-25 boundary, applied on a Task
branch, and fully rechecked. Upstream is never merged wholesale.

## App controls and host runtime

q mode sends semantic controls only to an already-running instance:

```bash
handy --toggle-transcription --herdr-pane "$HERDR_PANE_ID"
handy --cancel
```

The explicit pane is strictly validated and stored only when the first command
starts an idle recorder. A stop ignores its supplied pane and retains the start
target through one exact Herdr delivery attempt, with no focus recapture or
focused-input fallback. Cancel is targetless and affects only workstation-local
recording or processing; it cannot cancel laptop/remote ownership.

These commands are fire-and-forget. They make no cold-start, readiness, or
acknowledgement claim. Legacy `handy --toggle-transcription` remains Auto-targeted,
and the installed Left-Control bridge and realtime signal compatibility path
remain supported. Startup also accepts `--start-hidden`, `--no-tray`, `--debug`,
and `--help`.

The package includes its private native libraries. The Linux host needs the GTK
layer-shell runtime and `xdotool`. For WebKit rendering trouble use
`WEBKIT_DISABLE_DMABUF_RENDERER=1`; when layer-shell is unsuitable use
`HANDY_NO_GTK_LAYER_SHELL=1`.
