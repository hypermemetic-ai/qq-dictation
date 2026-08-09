# qq-dictation

`qq-dictation` is the private `qqp-dev/qq-dictation` Linux product for QQ
workstations and laptops. It keeps Handy's local speech recognition and adds
Herdr-pane target binding, the Right-Control dictation-mode bridge, and a pinned
per-user build and installation path.

There is no public support, discussion forum, or release channel for this
product.

## Runtime behavior

- Speech recognition, VAD, audio capture, model storage, and history remain local.
- On X11, a recording started in Herdr is delivered to the pane that was
  focused at recording start, even if focus changes before transcription
  finishes.
- Recordings started outside Herdr use focused Linux insertion.
- Right-Control arms or exits dictation mode; while armed, Space starts or stops
  recording and Delete cancels without leaving the mode.
- The workstation path and remote laptop protocol share the same serialized
  transcription pipeline.
- Whisper-family and ONNX speech models are supported with the configured local
  accelerator.

See [the local distribution guide](docs/local-distribution.md) for the pinned
build/install contract and [the project concepts](docs/project-concepts.md) for
QQ-specific vocabulary.

## Build and update

The installed app is updated only by building and installing an exact clean
Repository commit:

```bash
QQ_BUILD_MEM=8g scripts/build-local.sh
scripts/install-local.sh
```

The first command performs the pinned contained Linux build and creates the
local AppDir. The second installs it under
`~/.local/opt/qq-dictation/Handy.AppDir` for the current user. Read
[`BUILD-LESSONS.md`](BUILD-LESSONS.md) before running the build.

No self-updater or release-artifact path exists. Only the two commands above
update the installed app.

## Development checks

Install the lock-selected frontend dependencies, then run the ordinary checks:

```bash
bun install --frozen-lockfile
bun run check:translations
bun run lint
bun run build
```

Rust compilation, tests, and packaging run in the pinned contained environment;
see [BUILD.md](BUILD.md).

## Upstream provenance and selective intake

This product derives from [Handy](https://github.com/cjpais/Handy), used under
its MIT license. The unchanged terms are in [LICENSE](LICENSE). The exact Handy
baseline is:

```text
8a362e9eba59d4057fda79b7f38f5b0d5cbabf65
```

To consider later Handy work, fetch the `upstream` remote, inspect changes after
that baseline, choose only changes compatible with the current Linux and
TASK-25 workstation/laptop boundary, apply those changes deliberately on a Task
branch, and rerun this Repository's full checks. Never merge upstream wholesale.

## App controls

A running instance accepts these local commands:

```bash
handy --toggle-transcription
handy --toggle-post-process
handy --cancel
```

Startup flags include `--start-hidden`, `--no-tray`, `--debug`, and `--help`.
The debug settings shortcut is Ctrl+Shift+D.

## Linux runtime requirements

The contained package includes the application and its private native runtime
libraries. The host needs the GTK layer-shell runtime and `xdotool` for the
approved X11 insertion path. If WebKit rendering is unstable, launch with
`WEBKIT_DISABLE_DMABUF_RENDERER=1`; if layer-shell initialization is unsuitable
for the current desktop, use `HANDY_NO_GTK_LAYER_SHELL=1`.

Speech models remain under the ordinary per-user app-data path. Model download
origins under `handy-computer` are runtime inputs retained from Handy, not a
support or distribution channel for qq-dictation.
