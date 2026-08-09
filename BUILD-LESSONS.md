# Local build lessons

Read this ledger before running Rust checks or packaging the QQ Handy distribution. It records build-stage failures that produced a reusable lesson. It is not a substitute for fresh checks.

When a new failure teaches something transferable, append or revise an entry with:

- the stage that actually ran;
- the observed failure, without calling a later stage successful;
- the verified cause;
- the repository safeguard or working procedure;
- the evidence required to call the gate green.

Task notes retain ticket-specific evidence. This file carries only lessons that a later implementer or Adaptive Task Owner should apply. A delegated Rust work order must point the implementer to this file; if their substrate cannot run the documented gate, their envelope must say so and the accountable owner must run it before review or commit.

## 2026-07-24 and 2026-08-02 — Build memory containment changed with the toolchain

**Stage:** native release compilation.

**Observed failure:** On July 24, two unconstrained local builds caused kernel-confirmed global out-of-memory events and disrupted the desktop. Serializing Cargo/CMake in a 5 GiB, two-CPU container then completed with the older builder image. On August 2, after the builder image pulled a newer GCC, the 5 GiB container itself ran out of memory three times while `cc1plus` compiled ggml-vulkan's `mul_mm.comp.cpp`, even at one job. The host kernel records cgroup OOM kills at 12:09, 12:35, and 12:42; those runs did not complete.

**Cause:** Uncontained parallel compilation endangered the host in July. The resulting 5 GiB cap was safe for that toolchain, but the newer GCC's heaviest ggml-vulkan translation unit needed more than 5 GiB in August.

**Safeguard:** Preserve containment and serialization, but use the later proven allowance on this machine: `QQ_BUILD_MEM=8g scripts/build-local.sh`. That applies an 8 GiB memory limit and the same 8 GiB memory-swap limit (no additional container swap), two CPUs, `CARGO_BUILD_JOBS=1`, and `CMAKE_BUILD_PARALLEL_LEVEL=1`. The script's 5 GiB default documents the earlier/lighter case; it is not the current known-working limit for a rebuilt toolchain image. Apply the same 8 GiB boundary to ad hoc Rust test containers that can trigger native compilation.

**Green evidence:** For tests, rustfmt exits zero and the full Rust test target compiles and reports final counts. For a release, the container exits successfully, the expected AppDir is produced, its commit marker matches the built commit, and no new kernel or cgroup OOM event occurred. A bare, unconstrained, 5 GiB-OOM, or Docker-image-only run is not equivalent evidence.

**Sources:** completed TASK-3 records the July containment and successful older-image 5 GiB build; kernel journal entries on August 2 record the three later 5 GiB cgroup kills; PR #13 (`d439b74`) records the newer-GCC diagnosis and 8 GiB override; PR #15 (`4ac0015`) verifies forwarding `--memory 8g --memory-swap 8g`; the installed AppDir built later that day records merged commit `4c9fb207`.

## 2026-08-09 — Frontend Node needed an internal heap limit

**Stage:** Tauri's frontend `beforeBuildCommand`, `bun run build` (`tsc && vite build`), before Cargo/Rust compilation.

**Observed failure:** A contained release run kept the proven 8 GiB memory and memory-swap limits, two CPUs, and serialized Cargo/CMake jobs. Vite reached module transformation, then Bun reported that the build script was killed with `SIGKILL` and Tauri exited 137. Cargo/Rust compilation did not start in that run.

**Cause:** The kernel identified the container's Node process as the memory-cgroup OOM victim at the exact 8 GiB limit, with about 8.3 GiB anonymous RSS. The frontend process had consumed the build cgroup rather than leaving memory for later stages.

**Safeguard:** `scripts/build-local.sh` sets `NODE_OPTIONS=--max-old-space-size=4096` inside the container while preserving the 8 GiB operator-invoked container limit, equal memory-swap limit, two CPUs, and one Cargo/CMake job. Do not increase the container allowance to compensate for an unconstrained frontend heap.

**Green evidence:** A focused one-variable rerun used the same image, 8 GiB cgroup, two CPUs, source, dependencies, and frontend command, adding only the 4 GiB Node old-space cap. Vite transformed 2,201 modules and completed in 3.73 seconds with no new kernel OOM record. That verifies the frontend safeguard; a complete release still requires the separately reported Rust, packaging, AppDir, and no-new-OOM evidence.

**Sources:** TASK-27's 2026-08-09 contained-build output, kernel cgroup OOM record, and focused frontend memory diagnosis.

## 2026-07-24 — A full-suite failure exposed a false catalog claim

**Stage:** full Rust test suite and final CI.

**Observed failure:** The suite passed 145 tests but failed the catalog architecture check for `moss`.

**Cause:** The generated catalog advertised MOSS while pinned `transcribe-cpp 0.1.3` had no MOSS loader. Reproducing the same failure on the base established its origin but did not make the advertised compatibility true.

**Safeguard:** Compare an unexpected full-suite failure with the unchanged base, then fix an in-scope false claim rather than suppressing it or declaring an unsupported architecture known.

**Green evidence:** Focused catalog tests and the full suite both pass against the truthful generated catalog. Report pass, fail, and ignored counts separately.

## 2026-08-01 and 2026-08-05 — Worktree symlinks were not visible inside the build container

> **Superseded safeguard:** TASK-27 removed the checkout-link workaround. The
> failures and cause below remain historical evidence; the current lifecycle
> follows the external-root safeguard in this entry.

**Stage:** container setup and dependency installation before compilation.

**Observed failure:** The first TASK-6 build from an isolated Change worktree stopped because Cargo, target, and ORT cache symlinks resolved outside `/work`, where the container could not see them. TASK-14 then exposed two variants. First, its worktree had a root-owned empty `.docker-cache/` instead of the expected shared-cache symlinks, so the user-mapped container could not create `cargo`, `target`, or `ort`. After those links were repaired, `bun install` failed with `ENOENT` because worktree `node_modules` was another absolute symlink to the primary checkout, also outside the container mount. In every case the Docker image built, but application compilation never started.

**Cause:** Methodology-required worktrees contained machine-local inputs and absolute symlinks that Git did not carry into the container. The then-current `build-local.sh` could resolve and mount a shared cache **only when** `.docker-cache/cargo` was already a symlink; it did not provision missing links, repair ownership, or make any other outside-worktree symlink visible.

**Historical safeguard:** Before TASK-27, worktrees had to inventory every symlink with `find <worktree> -xdev -type l`, resolve complete chains, and prove that `.docker-cache/{cargo,target,ort}` alone reached writable primary-checkout cache directories. `node_modules` still had to be a real, user-owned, writable worktree directory rather than a link to the primary checkout. That per-worktree cache-link procedure is retained here as history, not current guidance.

**Current safeguard:** `scripts/build-local.sh` now creates and directly mounts `${XDG_CACHE_HOME:-$HOME/.cache}/qq-dictation/build/` for every checkout and worktree, exposing its `cargo`, `target`, and `ort` subdirectories only as `/qq-build-cache/...` in the container. The cache mount stays outside the repository bind at `/work`; a real run with a nested mount destination made Docker create an empty, root-owned checkout `.docker-cache` during container setup. A non-empty `XDG_CACHE_HOME`, or the `HOME` used for the default, must provide a safe absolute base; malformed or unknown input is refused rather than rewritten. The canonical cache root must be a real directory; both inspection and building refuse a symbolic link at that exact path before reading its metadata, creating cache children, or invoking Docker. No checkout-root `.docker-cache` symlink, mirror, or compatibility directory is supported. `.local-build/` remains checkout-local, and `node_modules` remains a real checkout-local dependency directory.

Run `scripts/build-cache.sh inspect` for read-only `key=value` owner, mode, size, entry-count, last-write, and rebuild-cost evidence. The pre-migration active cache measured 21,122,310,277 bytes, with 95,713 regular files, 16,182 directories, and 38 symlinks. Its Cargo, release-target, native C++/Vulkan, and ONNX Runtime state is expensive to recreate, so retain it; size alone is never deletion authority, and inspection always leaves quiescence unproven and pruning unauthorized.

Migration or a future prune requires fresh checks for build-related processes, Docker bind mounts, and open files below the candidate; unavailable or ambiguous evidence fails closed. On this host, stage migration by an atomic same-filesystem rename beside the canonical root, verify owner/mode, bytes, counts, and hash samples, then atomically rename staging to `build`. An interruption before staging leaves the old root active; an interruption after staging permits only verified resume or rename rollback while the old path is absent. A future explicitly authorized prune first renames `build` to a timestamped sibling quarantine. Retain it for rollback; restore it only if no new canonical root exists, and delete only that quarantine after the agreed window, renewed quiescence, and acceptance. Never touch unrelated XDG cache content.

**Green evidence:** The host-side mount is exactly the canonical external root; no checkout `.docker-cache` exists or is a symlink; the container reaches Bun, Rust, and packaging in sequence through `/qq-build-cache/{cargo,target,ort}`; and the checkout-local AppDir carries the built commit. Starting Docker, building the toolchain image, failing at cache creation, or failing at dependency-directory access is not a Rust/application build result.

**Sources:** completed TASK-6 implementation notes and commit `76fe322`; TASK-14 release output showing cache-directory permission errors before Bun/Cargo; the next TASK-14 release output showing cache preflight pass followed by `bun install ... ENOENT: could not open the "node_modules" directory` before Tauri/Cargo.

## 2026-08-02 — A comment broke a continued `docker run` command

**Stage:** release-build script invocation, before Docker started the build container.

**Observed failure:** The first PR #13 script put an explanatory comment inside a backslash-continued `docker run` command. Bash ended the command at that physical newline, so Docker received `docker run --rm` with no image or command and failed immediately.

**Cause:** A shell comment inside a line continuation is syntax, not harmless annotation.

**Safeguard:** Keep comments above continued shell commands. When editing `scripts/build-local.sh`, run `bash -n` and a stubbed argument-capture check that proves both `docker build` and `docker run` receive the expected arguments, including the selected memory and memory-swap values.

**Green evidence:** Shell syntax passes, argument capture shows the intended Docker invocation, and the real build reaches its independently reported compilation/package stages.

**Source:** PR #15 and commit `4ac0015`.

## 2026-08-05 — The build script could not reach Docker after `sudo -v`

**Stage:** final clean release build, before the builder image ran.

**Observed failure:** `sudo -v` succeeded, but `scripts/build-local.sh` invokes plain `docker`. The current operator account does not have direct access to `/var/run/docker.sock`, so Docker failed with `permission denied while trying to connect to the docker API`.

**Cause:** Caching sudo authorization does not change the permissions of a later unprivileged `docker` process. Running the entire build script under sudo is not an equivalent repair because the script derives the container UID/GID from its caller and would produce root-owned worktree outputs.

**Safeguard:** Keep the build script under the operator’s normal UID and narrowly wrap only its `docker` executable as `sudo /usr/bin/docker` for this host. Do not sudo the whole script and do not change Docker group membership as part of a release build.

**Green evidence:** The script completes through its final artifact checks, the embedded commit matches the clean source HEAD, and all generated worktree artifacts remain owned by the operator.

## 2026-08-05 — Git worktree metadata was outside the container mount

**Stage:** owner verification after container-side rustfmt, before compilation.

**Observed failure:** Rustfmt completed, then an ad hoc container command ran `git diff --check` inside `/work`. Git failed with `fatal: not a git repository` because the worktree’s `.git` file points to the primary checkout’s `.git/worktrees/...` metadata outside the mounted worktree. The fail-fast chain stopped, so compilation/tests did not start.

**Cause:** A Git worktree’s `.git` entry is a pointer file, not self-contained repository metadata. Symlink inventory does not reveal this outside-mount dependency.

**Safeguard:** Run Git cleanliness and `git diff --check` on the host before or after the container, where the worktree’s common Git directory exists. Keep the container phase to formatter/compiler/test/build commands unless the common Git metadata is deliberately mounted. Do not place a host-dependent Git check between container-side rustfmt and tests.

**Green evidence:** Host Git checks exit zero against the exact worktree, followed separately by container rustfmt check, compilation, and final test results.

## 2026-08-05 — The delegate substrate had no usable Rust runner

**Stage:** toolchain discovery before TASK-14 Rust checks.

**Observed failure:** `cargo` and `nix` were unavailable, and the agent process could not access the Docker socket directly.

**Cause:** The delegate/owner shell did not have a host Rust toolchain or Docker-group access. The repository's packaging image remained usable only through an operator-authorized Docker invocation.

**Safeguard:** An implementer without the substrate records Rust checks as not run rather than inferring success from frontend checks or LSP. The accountable owner uses the pinned `packaging/Dockerfile` image under a guarded operator authorization and preserves the TASK-3 resource limits.

**Green evidence:** Actual `cargo fmt -- --check` and `cargo test` output from the mounted Task worktree. Successfully building the Docker toolchain image proves only that the image built; it does not prove the Rust project compiled or its tests ran.

## 2026-08-05 — Unformatted delegate deltas repeatedly stopped TASK-14 before compilation

**Stage:** `cargo fmt -- --check`, before compilation.

**Observed failure:** The initial implementation and the later review-fix delta each came from a delegate substrate without Rust tooling. Rustfmt printed differences in `src-tauri/src/managers/history.rs` on both owner gates. Because each command chain was fail-fast, `cargo test` never started in either attempt.

**Cause:** The delegate could not run rustfmt, while owner-side frontend/LSP checks do not enforce Rust formatting. Manually correcting the first implementation did not protect a later delegated delta from reintroducing the same failure class.

**Safeguard:** After **every** Rust delta from a delegate that reports formatting not run, the owner first runs `cargo fmt` in the contained Rust environment and inspects the resulting source diff. Only then run the formal fail-fast sequence: `git diff --check`, `cargo fmt -- --check`, compilation, and tests. Do not wait for a check-only invocation to rediscover expected formatting work, and never summarize a toolchain-image build or formatting failure as a Rust build.

**Green evidence:** The formatter has been applied after the final delegated delta, the resulting changes are reviewed, rustfmt check exits zero, and separately visible compilation/test results follow.

## 2026-08-05 — A shared type change missed a test-only constructor

**Stage:** compilation of the full Rust test target after formatting passed.

**Observed failure:** Rust error `E0063` reported that `src-tauri/src/tray.rs` initialized `HistoryEntry` without the new `post_process_model` and `audio_available` fields. Tests did not run because the test binary did not compile.

**Cause:** TASK-14 updated production constructors and bindings but missed a `#[cfg(test)]` fixture in another module. Primary LSP diagnostics and focused inspection of the history module did not expose that test-target constructor.

**Safeguard:** When changing a shared Rust struct, search the entire crate for struct literals and update production and test-only constructors. Run the full test target, not only focused module tests.

**Green evidence:** The full test target compiles and `cargo test` reports its final pass/fail/ignored counts. Correct source plus clean LSP is not a substitute.
