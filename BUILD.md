# Linux build instructions

`qq-dictation` supports the private Linux workstation and laptop product.
The pinned contained build is the authoritative compilation and packaging path.

> [!IMPORTANT]
> Read [`BUILD-LESSONS.md`](BUILD-LESSONS.md) before Rust checks or packaging.
> The current proven workstation command uses an 8 GiB container limit, two
> CPUs, and one Cargo/CMake job. Within that boundary, the script caps Node's
> old-space heap at 4 GiB so frontend tooling cannot consume the full build
> cgroup.

## QQ contained-build cache lifecycle

`scripts/build-local.sh` is the only creator of the shared Linux contained-build
cache. Every checkout and linked worktree consumes the same host directory
directly:

```text
${XDG_CACHE_HOME:-$HOME/.cache}/qq-dictation/build/
```

`XDG_CACHE_HOME`, when non-empty, must be absolute. Otherwise `HOME` must be
available and absolute so the script can use `$HOME/.cache`. Invalid or unknown
input is refused before a cache is created or mounted; it is never rewritten to
a fallback. The canonical cache root must be a real directory; both inspection
and building refuse a symbolic link at that exact path before reading its
metadata, creating cache children, or invoking Docker. The host root is mounted
at `/qq-build-cache`, with `cargo`, `target`, and `ort` beneath it. This mount
stays outside the repository bind at `/work`: using a nested destination made
Docker create an empty, root-owned checkout `.docker-cache` during container
setup. No checkout may carry a `.docker-cache` symlink, mirror, or compatibility
directory. Only `.local-build/` remains checkout-local.

Inspect the root without authorizing a mutation:

```bash
scripts/build-cache.sh inspect
```

The command reports stable `key=value` evidence for the canonical root, creator,
filesystem owner and mode, bytes, entry counts, last write, and rebuild cost. It
always reports `quiescence=not_proven` and `prune_authorized=false`. Immediately
before this lifecycle change, the active cache held 21,122,310,277 bytes, 95,713
regular files, 16,182 directories, and 38 symlinks. Recreating its Cargo,
release-target, native C++/Vulkan, and ONNX Runtime state is a high-cost build,
so it is retained for reuse. Size alone never authorizes deletion.

A migration or future prune needs fresh quiescence evidence covering all three
consumer classes: running build-related processes, Docker container bind mounts,
and open files anywhere below the candidate root. A missing tool, permission
denial, race, or any other unknown fails closed. For the current same-filesystem
migration, first prove the old root quiescent and the canonical root absent;
atomically rename the old root to a uniquely named staging directory beside the
canonical root; verify owner, mode, byte and entry counts, plus recorded hash
samples; then atomically rename staging to `build`. Before the first rename the
old root remains authoritative. If interrupted while staged, run no build and
either resume verification and the final rename or, while the old path remains
absent, rename staging back to roll back. After the final rename, the canonical
root is authoritative and the checkout path stays absent.

Future pruning is a separate, explicitly authorized operation; the inspect
command cannot perform it. After the same fresh quiescence proof, atomically
rename `build` to a timestamped sibling quarantine and retain it through an
agreed rollback window. Rollback may rename it back only if no new canonical
root exists; if one does, stop rather than merge or discard either tree. Delete
only the exact quarantine after the retention window, renewed quiescence proof,
and acceptance that rollback is no longer needed. Unrelated XDG cache content
is never part of either transaction.

## Ordinary frontend checks

Use the lock-selected Bun version:

```bash
bun install --frozen-lockfile
bun run lint
bun run build
```

## Pinned contained package build

The host intentionally does not carry the complete GTK/WebKit and native speech
build stack. Build the deb package and extracted AppDir in the pinned Ubuntu
24.04 container:

```bash
QQ_BUILD_MEM=8g scripts/build-local.sh
```

The script:

1. refuses a dirty source tree;
2. builds the toolchain image from `packaging/Dockerfile`;
3. runs with fixed memory, CPU, Cargo, and CMake limits;
4. installs frontend dependencies from `bun.lock`;
5. builds only the Linux deb bundle;
6. extracts `.local-build/Handy.AppDir`; and
7. records the exact source commit as `qq-dictation-commit`.

Reusable build inputs remain in the external cache root described above. Only
`.local-build/` holds checkout-local build output.

## Install the local product

After a successful contained build:

```bash
scripts/install-local.sh
```

Installation is per-user. It places the AppDir under
`~/.local/opt/qq-dictation`, launchers under `~/.local/bin`, and the user service
under `~/.config/systemd/user`. The installer preserves existing app data and
backs up replaced local files.

There is no self-update, release download, or alternate package path. Updating
the installed app always requires a clean pinned build followed by the install
script.

## Native prerequisites represented by the container

The pinned image supplies Rust 1.96.0, Bun 1.3.3, the Tauri CLI selected by the
lock, C/C++ build tools, GTK/WebKit development libraries, ALSA, Vulkan shader
tools, and the native dependencies used by the speech engines. Keep changes to
that toolchain in `packaging/Dockerfile` so the build remains contained and
reviewable.

## Icons

`src-tauri/icons/logo.png` is the canonical icon source. Refresh only the three
Linux package images with:

```bash
scripts/generate-linux-icons.sh
```

The script requires the lock-selected `node_modules/.bin/tauri`, generates 32,
128, and 256 pixel PNGs in a temporary directory, and maps the 256 pixel output
to `128x128@2x.png`. It does not generate a platform matrix.

## Troubleshooting

- If native compilation is killed for memory, confirm the command used
  `QQ_BUILD_MEM=8g`; do not run an unconstrained host build.
- If a worktree cannot see the build cache, run
  `scripts/build-cache.sh inspect` and confirm it reports the canonical external
  root; checkout cache links are unsupported.
- If the application cannot load `libgtk-layer-shell.so.0`, install the host
  runtime package supplied by the Linux distribution.
- If WebKit rendering is unstable, set
  `WEBKIT_DISABLE_DMABUF_RENDERER=1` when launching.
- If GTK layer shell does not suit the current desktop, set
  `HANDY_NO_GTK_LAYER_SHELL=1` to use the notification-window fallback.
