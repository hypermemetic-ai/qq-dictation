#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || "$1" != inspect ]]; then
    printf 'Usage: %s inspect\n' "${0##*/}" >&2
    exit 64
fi

if [[ -n "${XDG_CACHE_HOME:-}" ]]; then
    cache_base="$XDG_CACHE_HOME"
elif [[ -n "${HOME:-}" ]]; then
    cache_base="${HOME}/.cache"
else
    printf 'Refusing to inspect: no absolute cache base is available from XDG_CACHE_HOME or HOME.\n' >&2
    exit 1
fi
if [[ "$cache_base" != /* || "$cache_base" == *$'\n'* || "$cache_base" == *$'\r'* ]]; then
    printf 'Refusing to inspect: cache base must be a safe absolute path.\n' >&2
    exit 1
fi
cache_root="${cache_base}/qq-dictation/build"

if [[ -L "$cache_root" ]]; then
    printf 'Refusing to inspect: canonical cache root must be a real directory; symbolic links are refused.\n' >&2
    exit 1
fi

printf 'canonical_root=%s\n' "$cache_root"
printf 'creator=ops/build/build-local.sh\n'

if [[ ! -e "$cache_root" && ! -L "$cache_root" ]]; then
    printf 'owner=not_present\n'
    printf 'mode=not_present\n'
    printf 'bytes=0\n'
    printf 'regular_files=0\n'
    printf 'directories=0\n'
    printf 'symlinks=0\n'
    printf 'last_write_utc=not_present\n'
else
    if [[ ! -d "$cache_root" ]]; then
        printf 'Refusing to inspect: canonical cache root is not a directory.\n' >&2
        exit 1
    fi

    LC_ALL=C
    export LC_ALL
    owner="$(stat -c '%U:%G' -- "$cache_root")"
    mode="$(stat -c '%a' -- "$cache_root")"
    du_output="$(du -sb -- "$cache_root")"
    bytes="${du_output%%[[:space:]]*}"
    regular_files="$(find "$cache_root" -type f -printf . | wc -c | tr -d '[:space:]')"
    directories="$(find "$cache_root" -type d -printf . | wc -c | tr -d '[:space:]')"
    symlinks="$(find "$cache_root" -type l -printf . | wc -c | tr -d '[:space:]')"
    last_write_epoch="$(find "$cache_root" -printf '%T@\n' | awk '
        NR == 1 || $1 > latest { latest = $1 }
        END { print latest }
    ')"
    last_write_utc="$(date -u --date="@${last_write_epoch}" '+%Y-%m-%dT%H:%M:%SZ')"

    printf 'owner=%s\n' "$owner"
    printf 'mode=%s\n' "$mode"
    printf 'bytes=%s\n' "$bytes"
    printf 'regular_files=%s\n' "$regular_files"
    printf 'directories=%s\n' "$directories"
    printf 'symlinks=%s\n' "$symlinks"
    printf 'last_write_utc=%s\n' "$last_write_utc"
fi

printf 'rebuild_cost=high_native_rust_cpp_and_ort_rebuild\n'
printf 'quiescence=not_proven\n'
printf 'prune_authorized=false\n'
