#!/usr/bin/env bash
# Prove one exact committed qq-dictation tree in a fresh clone and emit its receipt.
set -euo pipefail
umask 077

expected_node='v22.22.3'
expected_pi='0.84.1'
expected_bun='1.3.3'
expected_rust='rustc 1.96.0 (ac68faa20 2026-05-25)'
builder_image='qq-dictation-builder:ubuntu24.04'

fail() {
  local message="$*"
  if [ -n "${log_path:-}" ] && [ -f "$log_path" ]; then
    printf 'qq-check: %s\n' "$message" >>"$log_path"
    printf 'qq-check: %s (full log: %s)\n' "$message" "$log_path" >&2
  else
    printf 'qq-check: %s\n' "$message" >&2
  fi
  exit 1
}

[ "$#" -le 1 ] || fail 'usage: tools/check.sh [target-commit]'
if [ "$#" -eq 0 ]; then
  target=HEAD
else
  target="$1"
fi
[ -n "$target" ] || fail 'target commit must not be empty'

self="$(readlink -f -- "${BASH_SOURCE[0]}")" \
  || fail 'cannot resolve tools/check.sh'
root="$(cd -- "$(dirname -- "$self")/.." && pwd -P)" \
  || fail 'cannot resolve the Repository root'
repo_root="$(git -C "$root" rev-parse --show-toplevel 2>/dev/null)" \
  || fail 'tools/check.sh is not inside a Git worktree'
repo_root="$(cd -- "$repo_root" && pwd -P)" \
  || fail 'cannot canonicalize the Repository root'
[ "$repo_root" = "$root" ] || fail 'tools/check.sh does not resolve at the Repository root'

if [[ "${XDG_STATE_HOME:-}" = /* ]]; then
  state_home="$XDG_STATE_HOME"
elif [[ "${HOME:-}" = /* ]]; then
  state_home="$HOME/.local/state"
else
  fail 'an absolute XDG_STATE_HOME or HOME is required for private Check state'
fi
state_root="$state_home/qq-dictation/checks"
mkdir -p -- "$state_root" || fail 'cannot create private Check state'
chmod 0700 -- "$state_root" || fail 'cannot secure private Check state'
run_dir="$(mktemp -d "$state_root/run.XXXXXXXX")" \
  || fail 'cannot allocate private Check run state'
run_dir="$(cd -- "$run_dir" && pwd -P)"
log_path="$run_dir/check.log"
: >"$log_path"
chmod 0600 -- "$log_path"
clone_dir="$run_dir/subject"
check_home="$run_dir/home"
cleanup() {
  rm -rf -- "$clone_dir" "$check_home"
}
trap cleanup EXIT HUP INT TERM

printf 'qq-check: source=%s\n' "$root" >>"$log_path"
printf 'qq-check: requested-target=%s\n' "$target" >>"$log_path"

revision_output="$run_dir/revision"
revision_error="$run_dir/revision.err"
if ! git -C "$root" rev-parse --verify --end-of-options "${target}^{commit}" \
    >"$revision_output" 2>"$revision_error"; then
  cat -- "$revision_error" >>"$log_path"
  fail 'target does not resolve to exactly one commit'
fi
if [ -s "$revision_error" ]; then
  cat -- "$revision_error" >>"$log_path"
  fail 'target commit is ambiguous'
fi
commit="$(cat -- "$revision_output")"
[[ "$commit" =~ ^[0-9a-f]{40}$ ]] \
  || fail 'target commit did not resolve to an exact lowercase 40-hex object id'
tree="$(git -C "$root" rev-parse --verify --end-of-options "${commit}^{tree}" 2>>"$log_path")" \
  || fail 'target Git tree is unavailable'
[[ "$tree" =~ ^[0-9a-f]{40}$ ]] \
  || fail 'target tree did not resolve to an exact lowercase 40-hex object id'
origin_url="$(git -C "$root" remote get-url origin 2>>"$log_path")" \
  || fail 'source origin remote is unavailable'
[ -n "$origin_url" ] || fail 'source origin remote is empty'

current_path="${PATH:-}"
[ -n "$current_path" ] || fail 'PATH is empty'
node_version="$(PATH="$current_path" node --version 2>>"$log_path")" \
  || fail 'Node version inspection failed'
[ "$node_version" = "$expected_node" ] \
  || fail "Node version mismatch: expected $expected_node, observed $node_version"
pi_version="$(PATH="$current_path" pi --version 2>>"$log_path")" \
  || fail 'Pi version inspection failed'
[ "$pi_version" = "$expected_pi" ] \
  || fail "Pi version mismatch: expected $expected_pi, observed $pi_version"
pi_bin="$(PATH="$current_path" command -v pi 2>>"$log_path")" \
  || fail 'Pi executable resolution failed'
[[ "$pi_bin" = /* && "$pi_bin" != *$'\n'* ]] \
  || fail 'Pi executable path is malformed'
pi_resolved="$(readlink -f -- "$pi_bin" 2>>"$log_path")" \
  || fail 'Pi executable cannot be resolved'
[[ "$pi_resolved" = /* && "$pi_resolved" != *$'\n'* ]] \
  || fail 'resolved Pi executable path is malformed'
if [[ "$pi_resolved" == */@earendil-works/pi-coding-agent/dist/cli.js ]]; then
  pi_package="${pi_resolved%/dist/cli.js}"
else
  npm_root="$(PATH="$current_path" npm root -g 2>>"$log_path")" \
    || fail 'global npm package root inspection failed'
  [[ -n "$npm_root" && "$npm_root" = /* && "$npm_root" != *$'\n'* ]] \
    || fail 'global npm package root is malformed'
  pi_package="$npm_root/@earendil-works/pi-coding-agent"
fi
pi_cli="$pi_package/dist/cli.js"
pi_manifest="$pi_package/package.json"
[ -f "$pi_cli" ] && [ -x "$pi_cli" ] \
  || fail 'the observed Pi package CLI is unavailable'
[ -f "$pi_manifest" ] || fail 'the observed Pi package manifest is unavailable'
pi_package_version="$(PATH="$current_path" node -e \
  'process.stdout.write(require(process.argv[1]).version)' \
  "$pi_manifest" 2>>"$log_path")" \
  || fail 'Pi package identity inspection failed'
[ "$pi_package_version" = "$expected_pi" ] \
  || fail "Pi package version mismatch: expected $expected_pi, observed $pi_package_version"

# Delegated Checks isolate generic XDG cache writes under their private run.
# That harness-only override is not the product's A-27 build-cache migration,
# so consume the already-established HOME cache without creating a second root.
if [[ "${QQ_DISPATCH_RUN_DIR:-}" = /* \
    && "${XDG_CACHE_HOME:-}" = "${QQ_DISPATCH_RUN_DIR}/cache" ]]; then
  [[ "${HOME:-}" = /* ]] \
    || fail 'an absolute HOME is required with the delegated XDG cache override'
  cache_base="$HOME/.cache"
elif [[ -n "${XDG_CACHE_HOME:-}" ]]; then
  cache_base="$XDG_CACHE_HOME"
elif [[ -n "${HOME:-}" ]]; then
  cache_base="$HOME/.cache"
else
  fail 'no absolute contained-build cache base is available'
fi
[[ "$cache_base" = /* && "$cache_base" != *$'\n'* && "$cache_base" != *$'\r'* ]] \
  || fail 'contained-build cache base must be a safe absolute path'
cache_root="$cache_base/qq-dictation/build"
[ ! -L "$cache_root" ] || fail 'canonical contained-build cache root must not be a symlink'
[ -d "$cache_root" ] || fail 'canonical contained-build cache root is unavailable; create it only through ops/build/build-local.sh'
for cache_child in cargo target ort; do
  [ -d "$cache_root/$cache_child" ] \
    || fail "canonical contained-build cache is missing $cache_child"
done

docker_bin="$(PATH="$current_path" command -v docker 2>>"$log_path")" \
  || fail 'Docker executable resolution failed'
[[ "$docker_bin" = /* && "$docker_bin" != *$'\n'* ]] \
  || fail 'Docker executable path is malformed'
builder_id="$("$docker_bin" image inspect --format '{{.Id}}' "$builder_image" 2>>"$log_path")" \
  || fail 'pinned contained builder image is unavailable; build it through ops/build/build-local.sh'
[[ "$builder_id" =~ ^sha256:[0-9a-f]{64}$ ]] \
  || fail 'contained builder image identity is malformed'

printf 'qq-check: commit=%s\n' "$commit" >>"$log_path"
printf 'qq-check: tree=%s\n' "$tree" >>"$log_path"
printf 'qq-check: node=%s\n' "$node_version" >>"$log_path"
printf 'qq-check: pi=%s\n' "$pi_version" >>"$log_path"
printf 'qq-check: cache=%s\n' "$cache_root" >>"$log_path"
printf 'qq-check: builder=%s\n' "$builder_id" >>"$log_path"

run_fresh_clone_backend() {
  local clone_status

  git clone --no-checkout --no-hardlinks -- "$root" "$clone_dir" || return 75
  git -C "$clone_dir" remote set-url origin "$origin_url" || return 76
  git -c advice.detachedHead=false -C "$clone_dir" checkout --detach "$commit" || return 77
  [ "$(git -C "$clone_dir" rev-parse HEAD)" = "$commit" ] || return 70
  [ "$(git -C "$clone_dir" rev-parse 'HEAD^{tree}')" = "$tree" ] || return 71
  mkdir -p -- "$check_home" || return 78
  chmod 0700 -- "$check_home" || return 79

  (
    cd -- "$clone_dir" || exit 80
    env -i \
      HOME="$check_home" \
      PATH=/usr/bin:/bin \
      LANG=C.UTF-8 \
      PYTHONDONTWRITEBYTECODE=1 \
      /usr/bin/python3 -W error -m unittest discover -s tests -p 'test_*.py'
  ) || return 80

  "$docker_bin" run --rm \
    --memory 8g \
    --memory-swap 8g \
    --cpus 2 \
    --user "$(id -u):$(id -g)" \
    --volume "$clone_dir:/work" \
    --volume "$cache_root:/qq-build-cache" \
    --volume "$check_home:/tmp/qq-check-home" \
    --workdir /work \
    --env HOME=/tmp/qq-check-home \
    --env NODE_OPTIONS=--max-old-space-size=4096 \
    --env CARGO_HOME=/qq-build-cache/cargo \
    --env CARGO_TARGET_DIR=/qq-build-cache/target \
    --env ORT_CACHE_DIR=/qq-build-cache/ort \
    --env CARGO_BUILD_JOBS=1 \
    --env CMAKE_BUILD_PARALLEL_LEVEL=1 \
    "$builder_image" \
    bash -lc "
      set -euo pipefail
      test \"\$(bun --version)\" = '$expected_bun'
      test \"\$(rustc --version)\" = '$expected_rust'
      bun install --frozen-lockfile
      bun run lint
      bun run format:check
      bun run build
      cargo test --manifest-path src-tauri/Cargo.toml
    " || return 81

  [ "$(git -C "$clone_dir" rev-parse HEAD)" = "$commit" ] || return 72
  [ "$(git -C "$clone_dir" rev-parse 'HEAD^{tree}')" = "$tree" ] || return 73
  git -C "$clone_dir" diff --check || return 82
  clone_status="$(git -C "$clone_dir" status --porcelain)" || return 83
  [ -z "$clone_status" ] || return 74
}

printf 'qq-check: backend=fresh-clone-contained-env\n' >>"$log_path"
if ! run_fresh_clone_backend >>"$log_path" 2>&1; then
  fail 'fresh-clone Check failed'
fi

printf 'qq-check: result=pass\n' >>"$log_path"
timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  || fail 'cannot record the receipt timestamp'
case "$log_path" in
  *$'\n'* | *$'\r'* | *$'\t'*) fail 'full log path contains unsupported control characters' ;;
esac
printf 'qq-check-receipt/v1 commit=%s tree=%s timestamp=%s node=%s pi=%s log=%s\n' \
  "$commit" "$tree" "$timestamp" "$node_version" "$pi_version" "$log_path"
