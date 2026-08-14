# Operations and build guide

This is the current operations authority for the private Linux/X11
`qq-dictation` product. It combines the build, local distribution, build-safety,
local-concept, install, and rollback contracts. The focused
[remote laptop guide](remote-laptop-dictation.md) and
[wire protocol](remote-dictation-protocol.md) remain separate because they are
used independently during A-25 laptop operations.

## Product and reproducible inputs

The product preserves Handy's local ASR, selected model and dictated language,
history and WAV retention, Herdr delivery, the
Right-Control mode bridge, and the A-25 workstation/laptop path. It supports the
private Linux/X11 workflow only; it has no public release, updater, alternate
artifact download, other-platform, or translated-UI contract.

Pinned contained inputs:

- Ubuntu 24.04 builder: `ops/build/Dockerfile`
- Rust 1.96.0
- Bun 1.3.3 and the exact `bun.lock`
- Handy upstream baseline `8a362e9eba59d4057fda79b7f38f5b0d5cbabf65`

The current proven build boundary is 8 GiB memory and the same 8 GiB
memory-swap limit (no additional container swap), two CPUs, one Cargo job, one
CMake job, and a 4 GiB Node old-space cap. Two earlier facts explain why these
limits are not optional: unconstrained native builds caused host OOM events,
and the newer GCC later OOM-killed ggml-vulkan under the old 5 GiB limit. A
contained frontend build also reached the 8 GiB cgroup until Node old space was
capped at 4 GiB. Do not run unconstrained host builds or increase the boundary
without new authorization and evidence.

## Contained-build cache lifecycle

`ops/build/build-local.sh` is the only creator of the shared build cache. Every
checkout and linked worktree directly consumes:

```text
${XDG_CACHE_HOME:-$HOME/.cache}/qq-dictation/build/
```

A nonempty `XDG_CACHE_HOME`, or `HOME` when it supplies `.cache`, must identify a
safe absolute base. Unknown, relative, newline-bearing, or carriage-return
input is refused rather than rewritten. The canonical root must be a real
directory; inspection and building refuse a symbolic link at that exact path
before reading metadata, creating children, or invoking Docker. Its `cargo`,
`target`, and `ort` children are mounted at `/qq-build-cache/...`, outside the
Repository bind at `/work`. No checkout `.docker-cache` directory, symlink,
mirror, or compatibility path is supported. `node_modules/` remains a real
checkout-local dependency directory and `.local-build/` is the only
checkout-local build output.

Read-only inspection is:

```bash
ops/build/build-cache.sh inspect
```

It emits stable `key=value` facts for root, creator, owner, mode, bytes, entry
counts, last write, and rebuild cost. It always says quiescence is unproven and
pruning is unauthorized. The retained pre-migration cache measured
21,122,310,277 bytes, 95,713 regular files, 16,182 directories, and 38 symlinks;
it contains expensive Cargo, release-target, native C++/Vulkan, and ONNX Runtime
state. Size alone never authorizes deletion.

Migration or pruning needs separate explicit authorization and fresh
quiescence evidence for all build-related processes, Docker bind mounts, and
open files below the candidate. Missing tools, denied permissions, races, or
ambiguous evidence fail closed. For same-filesystem migration, prove the old
root quiescent and the canonical root absent; atomically rename the old root to
a unique sibling staging directory; verify owner, mode, byte/entry counts, and
recorded hash samples; then atomically rename staging to `build`. Before staging
the old root is authoritative. If interrupted while staged, run no build and
either complete verification/final rename or rename staging back while the old
path remains absent.

Pruning is a later transaction, never an `inspect` action. After renewed
quiescence proof, atomically rename `build` to one timestamped sibling
quarantine and retain it for the agreed rollback window. Restore it only if no
new canonical root exists; otherwise stop without merging or deleting either
tree. Delete only that exact quarantine after the window, renewed quiescence,
and acceptance that rollback is no longer needed. Unrelated XDG cache content
is never part of either transaction.

## Build and package

The authoritative package build is:

```bash
QQ_BUILD_MEM=8g ops/build/build-local.sh
```

The script refuses staged, unstaged, or untracked source changes; builds the
pinned toolchain image under the same resource bounds; installs lock-selected
frontend dependencies; builds only the Linux deb bundle; extracts
`.local-build/Handy.AppDir`; installs `ops/package/AppRun`; and records the exact
source commit as `qq-dictation-commit`.

A green release requires the container to complete, the AppDir to exist, its
marker to equal the source commit, its files to remain operator-owned, and no
new host/cgroup OOM event. Building only the image, reaching only Bun, or failing
before Rust/package completion is not release evidence. Keep comments outside
backslash-continued shell commands. Run Git cleanliness and diff checks on the
host because a linked worktree's `.git` pointer is outside the container mount;
do not put a host-dependent Git command between container formatting and tests.
Use the operator's normal UID; if this host needs elevated Docker access,
narrowly wrap only the Docker executable. Never run the whole build script
under sudo and do not change Docker membership. After any Rust edit, rustfmt
must pass before the full test target compiles and reports its final counts;
source inspection or a language server is not a substitute.

The host intentionally lacks the full GTK/WebKit/native speech build stack. The
pinned image supplies the Tauri-selected CLI, C/C++ tools, GTK/WebKit headers,
ALSA, Vulkan shader tools, and speech-engine dependencies. Toolchain changes
belong only in `ops/build/Dockerfile`.

Ordinary frontend checks are:

```bash
bun install --frozen-lockfile
bun run lint
bun run build
```

The exact committed full Check is:

```bash
tools/check.sh "$(git rev-parse HEAD)"
```

It clones the commit without hardlinks into private run state, verifies the
commit and tree, uses the pinned contained toolchain under the proven resource
limits, runs ESLint, Repository-wide Prettier plus rustfmt check,
TypeScript and both Vite entry points, the Python suite with `python-xlib`, and
all Cargo tests. It emits one `qq-check-receipt/v1` line; the private absolute
log path in that receipt holds complete output. A receipt proves only that
exact commit and tree.

To refresh the three Linux package icons from `src-tauri/icons/logo.png` after
installing lock-selected dependencies:

```bash
ops/build/generate-linux-icons.sh
```

To regenerate the checked-in model catalog deliberately (this performs the
helper's documented network reads):

```bash
uv run ops/build/gen_catalog.py src-tauri/src/catalog/catalog.json
```

## Install, settings activation, and rollback

After a successful exact-commit build, the operator-visible per-user install is:

```bash
ops/install/install-local.sh
```

It installs the AppDir at `~/.local/opt/qq-dictation/Handy.AppDir`, launchers
under `~/.local/bin`, and `handy-ptt.service` under
`~/.config/systemd/user`. It preserves existing app data, ASR models, history,
WAV policy, and logs. Replaced launchers, bridge, service, settings, and prior
AppDir receive retained backups before replacement. There is no implicit
update: every update is a clean contained build followed by this installer.

The installer enables Herdr binding, push-to-talk, auto-submit, and the
minimal overlay. It does not install a second-pass prompt or provider.
No source-level Check reads live settings or credentials.

Rollback stops at the affected boundary. A failed pre-install build changes no
installed product. The installer retains the prior AppDir and per-file backups;
restore only the named prior AppDir/files, reload the user service when its unit
was restored, and restart the restored executable. Do not merge backup and new
AppDir trees. Cache migration/prune rollback follows the separate atomic rules
above, never product-install rollback.

The packaged host needs the GTK layer-shell runtime and `xdotool`. If WebKit is
unstable, launch with `WEBKIT_DISABLE_DMABUF_RENDERER=1`. If layer-shell is
unsuitable, use `HANDY_NO_GTK_LAYER_SHELL=1` for the notification-window
fallback.

## Runtime contracts

The local ASR model produces the raw transcription. There is no LLM second
pass. Deterministic custom-word and filler handling remain in the normal
transcription path.

A workstation recording that begins in Herdr captures one pane at start and
later delivers only to that pane; identified capture or delivery failures fail
closed rather than typing into current focus. Non-Herdr workstation recordings
retain focused X11 insertion.

The already-running app accepts
`handy --toggle-transcription --herdr-pane "$HERDR_PANE_ID"`. On an
Idle→Recording transition it strictly validates and stores that exact public
pane id. The stop invocation ignores its then-current pane, retains the start
target through processing, and attempts exactly one delivery to it. After
accepting an explicit target, the app never recaptures focus and never falls
back to OS input or another pane. `handy --toggle-transcription` without a pane
preserves Auto targeting. `handy --cancel` is targetless, idempotent, and applies
only to workstation-local recording or processing; laptop/remote ownership is
isolated. Both semantic commands are fire-and-forget controls for an
already-running instance only, with no cold-start, readiness, or acknowledgement
claim.

The installed bridge and realtime signal path remain unchanged and supported.
Right-Control arms/exits mode, Space starts/stops, and Delete cancels while
armed. The realtime signal contract remains prepare `SIGRTMIN+2`, mode-on `+3`,
mode-off `+4`, Space `+5`, and Delete `+6`; the legacy PTT pair remains
`SIGRTMIN`/`+1`. App or bridge replacement returns to mode off and releases
dynamic grabs.

The `transcription_history` table remains the source for the latest 1,000
successful takes, including older second-pass pairs that still have prompt and
provider-qualified model recorded. New takes write raw text only. WAV retention
is independent and defaults to the latest five
under PreserveLimit. `/dictation-review` takes no arguments, opens one
short-lived read-only SQLite snapshot, reads no audio, writes no live state,
and derives census/cohorts/samples from that fixed snapshot. It closely reviews
up to 30 distinct pairs (12 stratified-random, 10 risk-flagged, 8 latest from
the exact prompt/model cohort) and mines the full snapshot for custom-word
candidates appearing in at least three rows, reporting measured fuzzy-match
false positives. Sampling is not a confidentiality boundary; text cannot prove
ASR fidelity; subjective writing quality remains cited agent/operator judgment.

The remote laptop path is the A-25 Linux/X11 contract, not a second ASR system.
The workstation retains VAD, model/language/custom words, output
processing, history/WAV retention, cancellation, and either Herdr or
laptop-local delivery policy. Installation and one-laptop UAT remain reserved
operator steps. See the focused guides linked at the top for exact focus,
framing, health, effect-uncertainty, and failure behavior.

## Provenance, retired research, and selective upstream intake

The rejected CrisperWhisper evaluation remains recoverable from completed A-10
and research doc-7, plus Git history. It was introduced in commit
`25ef924afddd3063dacfce026327cdb40a99ff97`; the last containing ticket base is
`c83ced6f67226f66eed33f5035749d6732cd8b9f`. No prototype, service, dependency,
configuration, future-GPU stub, or operational claim remains in the current
tree.

For later Handy intake, fetch the `upstream` remote, inspect changes after
`8a362e9eba59d4057fda79b7f38f5b0d5cbabf65`, select only changes compatible
with the private Linux/X11 and A-25 boundaries, apply them deliberately on a
Task branch, and rerun the exact full Repository Check. Never merge upstream
wholesale.
