# Workstation remote-dictation protocol v1

`handy-remote-stream.py` is invoked through the existing authenticated SSH account. It copies every stdin/stdout byte, including across legal short writes, to `$XDG_RUNTIME_DIR/qq-dictation/remote.sock`; it owns no transcription, result, or targeting policy. The app creates that Unix socket as mode `0600` under an owner-only directory, verifies Linux same-user peer credentials, and opens no TCP listener or protocol credential.

Every request and response is one unsigned 32-bit big-endian byte length followed by one UTF-8 JSON object. Length is `1..65536` bytes. Objects reject unknown fields. Every object carries `"version":1`; another version is refused.

## Delivery modes

`start` may carry `"delivery_mode":"herdr"` or `"delivery_mode":"local"`. Omission means `herdr`, preserving protocol compatibility for clients installed before local delivery existed. Any other value or field is refused.

- `herdr` preserves the original remote path: the workstation owns delivery to one Herdr pane selected at commit.
- `local` returns one bounded injection plan to the owning laptop connection/request. The workstation does not inspect or require Herdr state and neither side captures a Space-start target.

Delivery mode is fixed in the per-install laptop configuration, not selected per request by the operator.

## Shared request sequence

1. Send `start` with the delivery mode and `{"format":"s16le","sample_rate":16000,"channels":1}`. In `herdr` mode, before recording the workstation obtains `herdr status server --json` for the configured/default session, requires live compatible status, and records only the owned Unix socket path/device/inode plus Linux peer PID/start identity and status version/protocol/session. It reads no pane, focus, or layout. In `local` mode it captures no Herdr identity and no laptop window. A valid start acquires the globally serialized workstation pipeline for that connection and app-minted request ID, starts VAD/streaming, and returns `recording`.
2. Send `audio` chunks naming that request ID. Each chunk contains 1–4800 signed 16-bit samples; one request accepts at most 9,600,000 samples (10 minutes). Valid audio enters the shared 30 ms VAD/frame buffer and selected-model streaming router.
3. Send `finish`. It returns `processing` while the workstation finalizes the stream, performs the configured second pass, and records history under the existing WAV policy.
4. Poll `status`. A nonblank result becomes `ready` and remains owned by the exact connection/request. Poll responses never contain result text or an injection plan. A blank successful result can become terminal `succeeded` without delivery. Processing cancellation reports `cancelling` until the completion guard is genuinely terminal.
5. After observing `ready`, the laptop performs the mode-specific automatic commit below. There is no new physical gesture. It sends at most one `commit` and never retries an effect-uncertain commit.

One helper connection may perform sequential non-overlapping requests while armed. The full request lifetime is 10 minutes. A 30-second read timeout disconnect-cancels an active request; idle timeout between requests is normal armed-mode idleness. Stale, duplicate, replayed, cross-connection, cross-request, out-of-order, malformed, truncated, oversized, early, and late controls are refused rather than normalized.

## Herdr commit

After `ready`, the laptop verifies that the exact Ghostty window ID/PID/title/class captured at start is still the active configured remote-Herdr surface, then sends its one commit.

Commit first consumes the ready request and re-observes the same public-status/socket/peer Herdr identity. Unavailable, malformed, incompatible, non-socket, wrong-owner, replaced-listener, peer PID, or process-start mismatch terminally fails before any focus snapshot or send. Exact identity equality begins the irrevocable boundary. The workstation then runs one bounded `herdr api snapshot` for that configured/default session, requires one syntactically valid `focused_pane_id` represented once as a live focused pane in that same snapshot, freezes it, and makes at most one literal `herdr pane send-text PANE_ID PAYLOAD` call. Auto-submit is one trailing carriage return in that payload. No second focus read, `pane get`, workstation X11/local capture, another pane, or fallback delivery is allowed. Replacement or response loss after identity equality can be effect-uncertain, so the client never retries.

The operator keeps the intended Herdr pane session-globally selected until delivery. Another Herdr client changing session-global focus before commit can redirect this remote result. This focus-sensitive rule is exclusive to the laptop-over-SSH Herdr path; workstation-local recording still captures and retains its exact target independently.

## Laptop-local commit and injection handoff

After `ready`, the local-mode laptop requires one readable current X11 focused/active window. This is only a pre-return refusal check; it saves no target. It then sends its one commit on the same owning helper connection and request.

The coordinator consumes the one `Ready` value before any result is exposed, serializing the handoff against cancellation, disconnect, timeout, replay, and cross-owner access. The one successful consuming commit response may contain:

```json
{
  "version": 1,
  "status": "succeeded",
  "request_id": "APP-MINTED-ID",
  "injection": {
    "text": "WORKSTATION-AUTHORED-FINAL-TEXT",
    "submit_key": "enter | ctrl_enter | cmd_enter | null"
  }
}
```

`injection.text` is nonempty and at most 8192 UTF-8 bytes. The workstation applies output processing and the configured trailing space before returning it. It also selects the configured auto-submit key, or `null` when auto-submit is disabled. The enclosing response remains inside the 65536-byte frame bound. The plan is absent from every status/poll, error, cancellation, failed commit, Herdr response, and later terminal/replay response. A different connection or request cannot consume it.

After receiving and strictly validating that plan, the laptop again requires a readable current X11 focus immediately before the effect. It does not compare this focus with either the pre-commit focus or any start-time window. It marks the injection attempted, invokes the configured absolute `xdotool` once as `type --clearmodifiers -- TEXT`, and, only if that text attempt reports success and `submit_key` is not null, invokes at most one corresponding `key --clearmodifiers ...` attempt. There is no retry, partial re-injection, saved-target activation, Herdr delivery, or other fallback after an absent focus, malformed handoff, timeout, adapter error, or uncertain effect.

The target is therefore whichever X11 window receives focus when `xdotool` performs delivery. The operator must keep the intended laptop window focused until text arrives. Direct X11 synthetic typing is best-effort: applications may reject or transform synthetic input, and Unicode, keyboard-layout, and IME behavior can vary. A zero exit from `xdotool` establishes only that the configured adapter accepted its one attempt, not that every application rendered identical text.

Cancellation, helper disconnect, timeout, app loss, or component replacement before the consuming response prevents laptop injection. Once the consuming response has been issued, the workstation result cannot be requested again; any subsequent focus or injection failure is a truthful local client failure with no retry or fallback.
