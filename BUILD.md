# Linux build instructions

`qq-dictation` supports the private Linux workstation and laptop product.
The pinned contained build is the authoritative compilation and packaging path.

> [!IMPORTANT]
> Read [`BUILD-LESSONS.md`](BUILD-LESSONS.md) before Rust checks or packaging.
> The current proven workstation command uses an 8 GiB container limit, two
> CPUs, and one Cargo/CMake job.

## Ordinary frontend checks

Use the lock-selected Bun version:

```bash
bun install --frozen-lockfile
bun run check:translations
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

Ignored `.docker-cache/` and `.local-build/` directories hold reusable build
inputs and output. In a linked worktree, follow the cache and `node_modules`
preflight in `BUILD-LESSONS.md` before starting the container.

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
- If a worktree container cannot see dependencies or caches, run the complete
  symlink preflight documented in `BUILD-LESSONS.md`.
- If the application cannot load `libgtk-layer-shell.so.0`, install the host
  runtime package supplied by the Linux distribution.
- If WebKit rendering is unstable, set
  `WEBKIT_DISABLE_DMABUF_RENDERER=1` when launching.
- If GTK layer shell does not suit the current desktop, set
  `HANDY_NO_GTK_LAYER_SHELL=1` to use the notification-window fallback.
