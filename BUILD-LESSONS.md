# Local build lessons

Read this ledger before running Rust checks or packaging the QQ Handy distribution. It records build-stage failures that produced a reusable lesson. It is not a substitute for fresh checks.

When a new failure teaches something transferable, append or revise an entry with:

- the stage that actually ran;
- the observed failure, without calling a later stage successful;
- the verified cause;
- the repository safeguard or working procedure;
- the evidence required to call the gate green.

Task notes retain ticket-specific evidence. This file carries only lessons that a later implementer or Change owner should apply. A delegated Rust work order must point the implementer to this file; if their substrate cannot run the documented gate, their envelope must say so and the accountable owner must run it before review or commit.

## 2026-07-24 and 2026-08-02 — Build memory containment changed with the toolchain

**Stage:** native release compilation.

**Observed failure:** On July 24, two unconstrained local builds caused kernel-confirmed global out-of-memory events and disrupted the desktop. Serializing Cargo/CMake in a 5 GiB, two-CPU container then completed with the older builder image. On August 2, after the builder image pulled a newer GCC, the 5 GiB container itself ran out of memory three times while `cc1plus` compiled ggml-vulkan's `mul_mm.comp.cpp`, even at one job. The host kernel records cgroup OOM kills at 12:09, 12:35, and 12:42; those runs did not complete.

**Cause:** Uncontained parallel compilation endangered the host in July. The resulting 5 GiB cap was safe for that toolchain, but the newer GCC's heaviest ggml-vulkan translation unit needed more than 5 GiB in August.

**Safeguard:** Preserve containment and serialization, but use the later proven allowance on this machine: `QQ_BUILD_MEM=8g scripts/build-local.sh`. That applies an 8 GiB memory limit and the same 8 GiB memory-swap limit (no additional container swap), two CPUs, `CARGO_BUILD_JOBS=1`, and `CMAKE_BUILD_PARALLEL_LEVEL=1`. The script's 5 GiB default documents the earlier/lighter case; it is not the current known-working limit for a rebuilt toolchain image. Apply the same 8 GiB boundary to ad hoc Rust test containers that can trigger native compilation.

**Green evidence:** For tests, rustfmt exits zero and the full Rust test target compiles and reports final counts. For a release, the container exits successfully, the expected AppDir is produced, its commit marker matches the built commit, and no new kernel or cgroup OOM event occurred. A bare, unconstrained, 5 GiB-OOM, or Docker-image-only run is not equivalent evidence.

**Sources:** completed TASK-3 records the July containment and successful older-image 5 GiB build; kernel journal entries on August 2 record the three later 5 GiB cgroup kills; PR #13 (`d439b74`) records the newer-GCC diagnosis and 8 GiB override; PR #15 (`4ac0015`) verifies forwarding `--memory 8g --memory-swap 8g`; the installed AppDir built later that day records merged commit `4c9fb207`.

## 2026-07-24 — A full-suite failure exposed a false catalog claim

**Stage:** full Rust test suite and final CI.

**Observed failure:** The suite passed 145 tests but failed the catalog architecture check for `moss`.

**Cause:** The generated catalog advertised MOSS while pinned `transcribe-cpp 0.1.3` had no MOSS loader. Reproducing the same failure on the base established its origin but did not make the advertised compatibility true.

**Safeguard:** Compare an unexpected full-suite failure with the unchanged base, then fix an in-scope false claim rather than suppressing it or declaring an unsupported architecture known.

**Green evidence:** Focused catalog tests and the full suite both pass against the truthful generated catalog. Report pass, fail, and ignored counts separately.

## 2026-08-01 and 2026-08-05 — Worktree symlinks were not visible inside the build container

**Stage:** container setup and dependency installation before compilation.

**Observed failure:** The first TASK-6 build from an isolated Change worktree stopped because Cargo, target, and ORT cache symlinks resolved outside `/work`, where the container could not see them. TASK-14 then exposed two variants. First, its worktree had a root-owned empty `.docker-cache/` instead of the expected shared-cache symlinks, so the user-mapped container could not create `cargo`, `target`, or `ort`. After those links were repaired, `bun install` failed with `ENOENT` because worktree `node_modules` was another absolute symlink to the primary checkout, also outside the container mount. In every case the Docker image built, but application compilation never started.

**Cause:** Methodology-required worktrees contain machine-local inputs and absolute symlinks that Git does not carry into the container. `build-local.sh` can resolve and mount a shared cache **only when** `.docker-cache/cargo` is already a symlink; it does not provision missing links, repair ownership, or make any other outside-worktree symlink visible.

**Safeguard:** Before any worktree build, inventory **all** symlinks with `find <worktree> -xdev -type l`, resolve each complete link chain with `readlink -f`, flag broken links, and classify every canonical destination as inside or outside the mounted worktree. This includes relative links whose chain ends at an absolute outside path. The only supported build-relevant outside links are `.docker-cache/cargo`, `.docker-cache/target`, and `.docker-cache/ort`: require them to resolve to the primary checkout's corresponding writable cache directories, which current `scripts/build-local.sh` mounts through their resolved parent. Require `node_modules` to be a real, user-owned, writable worktree directory so container-side `bun install --frozen-lockfile` can populate it; do not point or bind it to the primary checkout's mutable dependencies. Documentation/Backlog links may resolve outside because they are not build inputs.

**Green evidence:** The preflight lists every worktree symlink, including relative/chained/broken links, and shows that each build-relevant path is either a real writable worktree directory or one of the three script-supported cache links. Container setup reaches Bun, Rust, and packaging in sequence. Starting Docker, building the toolchain image, failing at cache creation, or failing at dependency-directory access is not a Rust/application build result.

**Sources:** completed TASK-6 implementation notes and commit `76fe322`; TASK-14 release output showing cache-directory permission errors before Bun/Cargo; the next TASK-14 release output showing cache preflight pass followed by `bun install ... ENOENT: could not open the "node_modules" directory` before Tauri/Cargo.

## 2026-08-02 — A comment broke a continued `docker run` command

**Stage:** release-build script invocation, before Docker started the build container.

**Observed failure:** The first PR #13 script put an explanatory comment inside a backslash-continued `docker run` command. Bash ended the command at that physical newline, so Docker received `docker run --rm` with no image or command and failed immediately.

**Cause:** A shell comment inside a line continuation is syntax, not harmless annotation.

**Safeguard:** Keep comments above continued shell commands. When editing `scripts/build-local.sh`, run `bash -n` and a stubbed argument-capture check that proves both `docker build` and `docker run` receive the expected arguments, including the selected memory and memory-swap values.

**Green evidence:** Shell syntax passes, argument capture shows the intended Docker invocation, and the real build reaches its independently reported compilation/package stages.

**Source:** PR #15 and commit `4ac0015`.

## 2026-08-05 — The delegate substrate had no usable Rust runner

**Stage:** toolchain discovery before TASK-14 Rust checks.

**Observed failure:** `cargo` and `nix` were unavailable, and the agent process could not access the Docker socket directly.

**Cause:** The delegate/owner shell did not have a host Rust toolchain or Docker-group access. The repository's packaging image remained usable only through an operator-authorized Docker invocation.

**Safeguard:** An implementer without the substrate records Rust checks as not run rather than inferring success from frontend checks or LSP. The accountable owner uses the pinned `packaging/Dockerfile` image under a guarded operator authorization and preserves the TASK-3 resource limits.

**Green evidence:** Actual `cargo fmt -- --check` and `cargo test` output from the mounted Change worktree. Successfully building the Docker toolchain image proves only that the image built; it does not prove the Rust project compiled or its tests ran.

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
