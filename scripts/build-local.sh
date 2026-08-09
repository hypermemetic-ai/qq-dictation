#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
builder_image="qq-dictation-builder:ubuntu24.04"
cache_dir="${repository_root}/.docker-cache"
output_dir="${repository_root}/.local-build"

if ! git -C "$repository_root" diff --quiet \
    || ! git -C "$repository_root" diff --cached --quiet \
    || [[ -n "$(git -C "$repository_root" ls-files --others --exclude-standard)" ]]; then
    printf 'Refusing to build from a dirty source tree; commit the exact source first.\n' >&2
    exit 1
fi

mkdir -p "$cache_dir" "$output_dir"

# Task worktrees share the primary checkout's expensive build cache through
# symlinks. Mount that resolved cache root over /work/.docker-cache so those
# links do not point outside the container's filesystem.
container_cache_dir="$cache_dir"
if [[ -L "${cache_dir}/cargo" ]]; then
    container_cache_dir="$(dirname "$(readlink -f "${cache_dir}/cargo")")"
fi

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
    --volume "${container_cache_dir}:/work/.docker-cache" \
    --workdir /work \
    --env HOME=/tmp/qq-builder \
    --env CARGO_HOME=/work/.docker-cache/cargo \
    --env CARGO_TARGET_DIR=/work/.docker-cache/target \
    --env ORT_CACHE_DIR=/work/.docker-cache/ort \
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
