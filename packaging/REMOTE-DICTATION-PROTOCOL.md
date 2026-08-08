# Workstation remote-dictation protocol v1

The workstation helper `handy-remote-stream.py` is invoked through the existing SSH account. It copies every stdin/stdout byte, including across legal short writes, to `$XDG_RUNTIME_DIR/qq-dictation/remote.sock`; it has no transcription or target policy. The app creates that same-user Unix socket as mode `0600` beneath an owner-only directory, verifies Linux peer credentials, and has no TCP listener or protocol credential. The laptop lifecycle and guarded per-user installation are documented in `REMOTE-LAPTOP-DICTATION.md`.

Every message and response is one unsigned 32-bit big-endian byte length followed by one UTF-8 JSON object. The length is `1..65536` bytes. Objects reject unknown fields. Every object carries `"version":1`; another version is refused.

## Request sequence

1. The stream connection sends `{"type":"start","version":1,"audio":{"format":"s16le","sample_rate":16000,"channels":1}}` and receives `pending` plus an app-minted `request_id`.
2. The laptop invokes the installer-owned `prefix+alt+d` Herdr transport chord in band, using the configured current prefix followed by `alt+d`. Its actual Ghostty/SSH reachability remains part of the later live proof. Herdr runs `handy-remote-bind.py` detached with `HERDR_ACTIVE_PANE_ID`. The binder opens its own socket connection and sends `{"type":"bind","version":1,"pane_id":"..."}`. It succeeds only for the sole live pending claim and an exact live pane.
3. The stream connection polls `{"type":"status","version":1,"request_id":"..."}`. It must receive `bound` before sending audio. Audio is refused until that acknowledgement has been written on the stream connection.
4. Each chunk is `{"type":"audio","version":1,"request_id":"...","pcm":[...]}` where `pcm` contains 1–4800 signed 16-bit samples. At most 9,600,000 samples (10 minutes) are accepted for one request. Accepted samples enter the app's shared 30 ms VAD/frame buffer and streaming router.
5. `finish` changes status to `processing`; poll `status` until `succeeded`, `failed`, or `cancelled`. `cancel` is valid only for the owning connection and request.

The pending target claim lives for 5 seconds. The entire request lives for 10 minutes. A helper disconnect cancels its recording or processing request. Request IDs and binder claims are one-shot; stale, cross-connection, out-of-order, malformed, truncated, oversized, non-live, and replayed input is refused rather than normalized. The bound pane is immutable, and remote handling never consults workstation X11 capture or Herdr session-global focus.
