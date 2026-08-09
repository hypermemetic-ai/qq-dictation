# Workstation remote-dictation protocol v1

`handy-remote-stream.py` is invoked through the existing authenticated SSH account. It copies every stdin/stdout byte, including across legal short writes, to `$XDG_RUNTIME_DIR/qq-dictation/remote.sock`; it owns no transcription or targeting policy. The app creates that Unix socket as mode `0600` under an owner-only directory, verifies Linux same-user peer credentials, and opens no TCP listener or protocol credential.

Every message and response is one unsigned 32-bit big-endian byte length followed by one UTF-8 JSON object. Length is `1..65536` bytes. Objects reject unknown fields. Every object carries `"version":1`; another version is refused.

## Request sequence

1. Send `start` with `{"format":"s16le","sample_rate":16000,"channels":1}`. A valid start immediately acquires the globally serialized workstation pipeline for that connection and app-minted request ID, starts VAD/streaming, and returns `recording`. There is no binder, pending target, chord, request-start pane, or pre-audio target state.
2. Send `audio` chunks naming that request ID. Each chunk contains 1–4800 signed 16-bit samples; one request accepts at most 9,600,000 samples (10 minutes). Valid audio enters the shared 30 ms VAD/frame buffer and selected-model streaming router.
3. Send `finish`. It returns `processing` while the workstation finalizes the stream, performs any selected second pass, and records history under the existing WAV policy.
4. Poll `status`. A nonblank result becomes `ready` and remains owned by the exact connection/request. A blank successful result can become terminal `succeeded` without delivery. Processing cancellation reports `cancelling` until the completion guard is genuinely terminal.
5. After observing `ready`, the laptop verifies the exact Ghostty window ID/PID/title/class captured at start is still the active configured remote-Herdr surface. It then sends exactly one `commit` for that request automatically. It never retries an effect-uncertain commit.
6. Commit is the serialized irrevocable boundary. The workstation runs one bounded `herdr api snapshot` for the configured/default session, requires one syntactically valid `focused_pane_id` represented once as a live focused pane in that same snapshot, freezes it, and makes at most one literal `herdr pane send-text PANE_ID PAYLOAD` call. Auto-submit is one trailing carriage return in that payload. No second focus read, `pane get`, workstation X11/local capture, another pane, or fallback delivery is allowed.

The operator keeps the intended Herdr pane session-globally selected until delivery. Another Herdr client changing session-global focus before commit can redirect this remote result. This focus-sensitive rule is exclusive to the laptop-over-SSH path; workstation-local recording still captures and retains its exact target independently.

One helper connection may perform sequential non-overlapping requests while armed. The full request lifetime is 10 minutes. A 30-second read timeout disconnect-cancels an active request; idle timeout between requests is normal armed-mode idleness. Cancellation, helper disconnect, timeout, app loss, or request replacement before commit prevents later delivery. Stale, duplicate, replayed, cross-connection, cross-request, out-of-order, malformed, truncated, oversized, early, and late controls are refused rather than normalized.
