#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source_app_dir="${repository_root}/.local-build/Handy.AppDir"
install_parent="${HOME}/.local/opt/qq-dictation"
install_app_dir="${install_parent}/Handy.AppDir"
local_bin="${HOME}/.local/bin"
user_units="${HOME}/.config/systemd/user"
settings_path="${XDG_DATA_HOME:-${HOME}/.local/share}/com.pais.handy/settings_store.json"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
ready_path="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/qq-dictation-handy-ready"

if [[ ! -x "${source_app_dir}/AppRun" ]]; then
    printf 'Build artifact is missing; run ops/build/build-local.sh first.\n' >&2
    exit 1
fi

mkdir -p "$install_parent" "$local_bin" "$user_units"
/usr/bin/python3 "${repository_root}/ops/install/install-remote-workstation.py" \
    --home "$HOME"

staging_dir="$(mktemp -d "${install_parent}/Handy.AppDir.staging.XXXXXX")"
cleanup() {
    rm -rf "$staging_dir"
}
trap cleanup EXIT
cp -a "${source_app_dir}/." "$staging_dir/"

# Retire the workstation key-grab bridge before removing its active files.
# Missing legacy units are expected on fresh and repeated installations.
systemctl --user stop handy-ptt.service 2>/dev/null || true
systemctl --user disable handy-ptt.service 2>/dev/null || true
systemctl --user stop handy.service 2>/dev/null || true
pkill -u "$(id -u)" -x handy 2>/dev/null || true
for _ in {1..50}; do
    if ! pgrep -u "$(id -u)" -x handy >/dev/null; then
        break
    fi
    sleep 0.1
done
if pgrep -u "$(id -u)" -x handy >/dev/null; then
    printf 'The previous Handy process did not stop before installation.\n' >&2
    exit 1
fi
rm -f "$ready_path"

if [[ -d "$install_app_dir" ]]; then
    backup_dir="${install_parent}/Handy.AppDir.backup.${timestamp}.$$"
    mv "$install_app_dir" "$backup_dir"
    printf 'Previous qq-dictation AppDir retained at %s\n' "$backup_dir"
fi
mv "$staging_dir" "$install_app_dir"
trap - EXIT

for existing in "${local_bin}/handy" "${user_units}/handy.service"; do
    if [[ -e "$existing" && ! -e "${existing}.before-qq-dictation" ]]; then
        cp -a "$existing" "${existing}.before-qq-dictation"
    fi
done

install -m 0755 "${repository_root}/ops/package/handy" "${local_bin}/handy"
install -m 0644 \
    "${repository_root}/ops/install/handy.service" \
    "${user_units}/handy.service"
rm -f "${local_bin}/handy-ptt-bridge.py" "${user_units}/handy-ptt.service"

mkdir -p "$(dirname "$settings_path")"
if [[ -f "$settings_path" ]]; then
    cp -a "$settings_path" "${settings_path}.before-qq-dictation.${timestamp}"
else
    (umask 077; printf '{"settings": {}}\n' >"$settings_path")
fi
/usr/bin/python3 "${repository_root}/ops/install/configure-local-settings.py" \
    "$settings_path"

systemctl --user daemon-reload
systemctl --user enable handy.service
systemctl --user start handy.service

expected_executable="${install_app_dir}/usr/bin/handy"
handy_pids=()
handy_pid=""
actual_executable=""
marker_pid=""
marker_state=""
marker_extra=""
for _ in {1..100}; do
    mapfile -t handy_pids < <(pgrep -u "$(id -u)" -x handy 2>/dev/null || true)
    if [[ "${#handy_pids[@]}" -eq 1 ]]; then
        handy_pid="${handy_pids[0]}"
        actual_executable="$(readlink -f "/proc/${handy_pid}/exe" 2>/dev/null || true)"
        marker_pid=""
        marker_state=""
        marker_extra=""
        if [[ -r "$ready_path" ]]; then
            read -r marker_pid marker_state marker_extra <"$ready_path" || true
        fi
        if [[ "$actual_executable" == "$expected_executable" \
            && "$marker_pid" == "$handy_pid" \
            && "$marker_state" =~ ^(ready|prepared|armed)$ \
            && -z "$marker_extra" ]]; then
            break
        fi
    fi
    sleep 0.1
done

mapfile -t handy_pids < <(pgrep -u "$(id -u)" -x handy 2>/dev/null || true)
if [[ "${#handy_pids[@]}" -ne 1 ]]; then
    printf 'Expected exactly one running Handy process; found %s.\n' \
        "${#handy_pids[@]}" >&2
    exit 1
fi
handy_pid="${handy_pids[0]}"
actual_executable="$(readlink -f "/proc/${handy_pid}/exe" 2>/dev/null || true)"
if [[ "$actual_executable" != "$expected_executable" ]]; then
    printf 'Unexpected Handy executable after installation: %s\n' \
        "$actual_executable" >&2
    exit 1
fi
marker_pid=""
marker_state=""
marker_extra=""
if [[ -r "$ready_path" ]]; then
    read -r marker_pid marker_state marker_extra <"$ready_path" || true
fi
if [[ "$marker_pid" != "$handy_pid" \
    || ! "$marker_state" =~ ^(ready|prepared|armed)$ \
    || -n "$marker_extra" ]]; then
    printf 'Handy readiness marker does not match running pid %s: %s\n' \
        "$handy_pid" "$ready_path" >&2
    exit 1
fi

printf 'Installed qq-dictation at %s\n' "$install_app_dir"
printf '%s\n' \
    'Handy is managed by handy.service; q mode controls remain owned by qq/Herdr.'
