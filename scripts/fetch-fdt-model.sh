#!/usr/bin/env bash
set -euo pipefail

revision="677a8a0c20f23858e3c581977111a572999ee487"
repository="stillerman/fdt-disfluency-mini-11m"
default_data_dir="${XDG_DATA_HOME:-${HOME}/.local/share}/com.pais.handy"
target_dir="${HANDY_FDT_MODEL_DIR:-${default_data_dir}/text-cleanup/fdt-mini-11m}"
mkdir -p "$(dirname "$target_dir")"
staging_dir="$(mktemp -d "${target_dir}.staging.XXXXXX")"

cleanup() {
    rm -rf "$staging_dir"
}
trap cleanup EXIT

download() {
    local remote_name="$1"
    local local_name="${2:-$remote_name}"
    curl --proto '=https' --tlsv1.2 --fail --location --retry 3 \
        "https://huggingface.co/${repository}/resolve/${revision}/${remote_name}?download=true" \
        --output "${staging_dir}/${local_name}"
}

download onnx/model_quantized.onnx model_quantized.onnx
download tokenizer.json
download config.json

(
    cd "$staging_dir"
    sha256sum --check <<'CHECKSUMS'
277208ae7810af2c1b96e9972939ef2968e9fccd985c28ec2695aa472c54144f  model_quantized.onnx
2fc687b11de0bc1b3d8348f92e3b49ef1089a621506c7661fbf3248fcd54947e  tokenizer.json
5950a263d977482445208831688f2bd0c5bed390d94e98e3898199a8a29e1fe4  config.json
CHECKSUMS
)

cat >"${staging_dir}/manifest.json" <<MANIFEST
{
  "repository": "${repository}",
  "revision": "${revision}",
  "model_file": "model_quantized.onnx",
  "distribution": "downloaded for local use; not committed or bundled",
  "license_note": "Model metadata says Apache-2.0; upstream training data includes DailyDialog under CC BY-NC-SA 4.0."
}
MANIFEST

if [[ -d "$target_dir" ]]; then
    previous_dir="${target_dir}.previous"
    rm -rf "$previous_dir"
    mv "$target_dir" "$previous_dir"
fi
mv "$staging_dir" "$target_dir"
trap - EXIT

printf 'Installed pinned FDT model at %s\n' "$target_dir"
