#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
builder_image="qq-dictation-builder:ubuntu24.04"
output_dir="${repository_root}/.local-build"

if [[ -n "${XDG_CACHE_HOME:-}" ]]; then
    cache_base="$XDG_CACHE_HOME"
elif [[ -n "${HOME:-}" ]]; then
    cache_base="${HOME}/.cache"
else
    printf 'Refusing to build: no absolute cache base is available from XDG_CACHE_HOME or HOME.\n' >&2
    exit 1
fi
if [[ "$cache_base" != /* || "$cache_base" == *$'\n'* || "$cache_base" == *$'\r'* ]]; then
    printf 'Refusing to build: cache base must be a safe absolute path.\n' >&2
    exit 1
fi
cache_dir="${cache_base}/qq-dictation/build"

if [[ -L "$cache_dir" ]]; then
    printf 'Refusing to build: canonical cache root must be a real directory; symbolic links are refused.\n' >&2
    exit 1
fi

if ! git -C "$repository_root" diff --quiet \
    || ! git -C "$repository_root" diff --cached --quiet \
    || [[ -n "$(git -C "$repository_root" ls-files --others --exclude-standard)" ]]; then
    printf 'Refusing to build from a dirty source tree; commit the exact source first.\n' >&2
    exit 1
fi

mkdir -p "$cache_dir"/{cargo,target,ort} "$output_dir"

docker build \
    --file "${repository_root}/packaging/Dockerfile" \
    --tag "$builder_image" \
    "${repository_root}/packaging"

# The 5g default protects the host desktop during builds, as established by
# the July containment evidence. Newer toolchain images can need more for
# ggml-vulkan's heaviest translation units; override with QQ_BUILD_MEM=8g
# (applies to both memory and
# memory-swap) without editing this script. NOTE: bash does not allow
# comments inside a line-continued command — keep them above `docker run`.
docker run --rm \
    --memory "${QQ_BUILD_MEM:-5g}" \
    --memory-swap "${QQ_BUILD_MEM:-5g}" \
    --cpus 2 \
    --user "$(id -u):$(id -g)" \
    --volume "${repository_root}:/work" \
    --volume "${cache_dir}:/qq-build-cache" \
    --workdir /work \
    --env HOME=/tmp/qq-builder \
    --env NODE_OPTIONS=--max-old-space-size=4096 \
    --env CARGO_HOME=/qq-build-cache/cargo \
    --env CARGO_TARGET_DIR=/qq-build-cache/target \
    --env ORT_CACHE_DIR=/qq-build-cache/ort \
    --env CARGO_BUILD_JOBS=1 \
    --env CMAKE_BUILD_PARALLEL_LEVEL=1 \
    "$builder_image" \
    bash -lc '
        set -euo pipefail
        mkdir -p "$CARGO_HOME" "$CARGO_TARGET_DIR" "$ORT_CACHE_DIR"
        bun install --frozen-lockfile
        bun run tauri build --bundles deb \
            --config "{\"bundle\":{\"createUpdaterArtifacts\":false}}"
    '

deb_path="$(find "${cache_dir}/target/release/bundle/deb" -maxdepth 1 \
    -type f -name 'Handy_*_amd64.deb' -print -quit)"
if [[ -z "$deb_path" ]]; then
    printf 'No Handy deb bundle was produced.\n' >&2
    exit 1
fi

staging_dir="$(mktemp -d "${output_dir}/Handy.AppDir.staging.XXXXXX")"
cleanup() {
    rm -rf "$staging_dir"
}
trap cleanup EXIT

dpkg-deb --extract "$deb_path" "$staging_dir"
install -m 0755 "${repository_root}/packaging/AppRun" "${staging_dir}/AppRun"
git -C "$repository_root" rev-parse HEAD >"${staging_dir}/qq-dictation-commit"

final_dir="${output_dir}/Handy.AppDir"
rm -rf "$final_dir"
mv "$staging_dir" "$final_dir"
trap - EXIT

printf 'Built local AppDir at %s\n' "$final_dir"
