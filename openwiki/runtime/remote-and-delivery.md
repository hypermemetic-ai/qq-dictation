---
type: Runtime and protocol reference
title: Remote dictation and target-safe delivery
description: Source-grounded contract for local PTT, remote laptop ingress, framed protocol v1, Herdr target binding, laptop-local injection, installers, services, and fail-closed delivery behavior.
tags: [runtime, remote-dictation, ptt, herdr, x11, security]
---

# Remote dictation and target-safe delivery

This boundary turns local X11 controls or laptop audio into one coordinator-owned operation, then delivers finalized text exactly once. The shared transcription and history path is documented in [Dictation pipeline](dictation-pipeline.md); installation lifecycle and rollback belong in [Build, install, and Check](../operations/build-install-check.md).

## Ownership and entrypoints

| Surface | Responsibility |
|---|---|
| `remote::RemoteIngress::start/shutdown` | Create, serve, and identity-check the workstation Unix socket; frame and validate protocol v1; bind requests to connections; dispatch only through `TranscriptionCoordinator`. |
| `actions::{start,finish}_remote_operation` and coordinator `remote_*` methods | Enforce global local/remote exclusivity, audio/sample/lifetime limits, processing, cancellation, ready-value ownership, and consuming commit. |
| `handy-remote-stream.py` | SSH-invoked, policy-free, bidirectional byte bridge between stdio and the app socket. |
| `handy-remote-client.py` | Laptop X11 state machine, PipeWire capture, SSH transport, strict response validation, Herdr window gate, and optional one-shot local injection. |
| `handy-ptt-bridge.py` | Workstation-local `Control_L` dictation-mode bridge using readiness state, Unix signals, and passive X11 grabs. |
| `target_binding.rs` | Local start-bound pane capture and remote start/commit Herdr session identity checks. |
| `clipboard.rs`, `input.rs` | Herdr, direct typing, clipboard paste, external script, optional clipboard copy, and submit-key effects. |

`lib.rs` starts ingress as managed application state; the socket is not a Tauri command and exposes no TCP listener. Installed process entrypoints are `~/.local/bin/handy-remote-stream.py`, `handy-remote-client.py`, and `handy-ptt-bridge.py`.

## Protocol v1

Each message is a 4-byte unsigned big-endian payload length followed by one UTF-8 JSON object. The frame is **1–65,536 bytes**; all tagged request objects deny unknown fields and require `version: 1`.

| Request | Required body and valid transition | Response |
|---|---|---|
| `start` | `audio={format:"s16le",sample_rate:16000,channels:1}`; optional `delivery_mode` is `herdr` by default or `local` | `recording` plus app-minted `request_id` |
| `audio` | Owning `request_id`; `pcm` signed i16 samples. Client chunks are 1–4,800 samples; coordinator caps a request at 9,600,000 samples, ten minutes. | `accepted` |
| `finish` | Owning recording request | `processing` |
| `status` | Owning request | `recording`, `processing`, `ready`, `cancelling`, or terminal status |
| `cancel` | Owning nonterminal request | `cancelled` or `cancelling` |
| `commit` | Owning `ready` request; consumes the staged value | terminal `succeeded`/`failed`; only local mode may return `injection` |

Responses contain `version`, `status`, and optional `request_id`, `error`, or `injection`. `injection={text,submit_key}` is present only in the one successful local-mode consuming commit. Text is nonempty and at most 8,192 UTF-8 bytes after optional workstation-owned trailing space; `submit_key` is `enter`, `ctrl_enter`, `cmd_enter`, or null. Status polls never expose transcript text.

One connection owns at most one active request, but may run sequential requests. `RemoteIngress` gives each accepted peer a `connection_id`; `ConnectionSession::begin` stores the app-minted request ID, `authorize` requires the exact `(connection_id, request_id)` capability, and `retire` clears it only after terminal state. The coordinator independently matches the same owner. At most eight socket connections run concurrently. A 30-second read timeout is harmless while idle; during an active request it disconnects and cancels. Each request expires after ten minutes. Terminal status is retained only for the owning connection/request for 60 seconds, with at most eight terminal records.

Malformed, truncated, zero/oversized, wrong-version, unknown-field, stale, replayed, cross-owner, duplicate, early, and late messages receive an error where possible and close the connection. Disconnect during recording cancels immediately; during processing it requests cancellation and remains `cancelling` until the worker completes. Recording expiry cancels, processing expiry follows that same deferred cancellation, and ready expiry discards staged output and releases the pipeline. Finish-before-audio and early commit are rejected without transferring authority. A consuming commit removes `ready` before delivery, preventing replay; terminal response retires the connection-local request so sequential reuse is possible.

```mermaid
sequenceDiagram
    participant Laptop as Laptop client
    participant SSH as SSH stream helper
    participant Ingress as Workstation ingress
    participant Coord as Coordinator
    participant Delivery as Delivery boundary
    Laptop->>SSH: start with exact audio format
    SSH->>Ingress: framed JSON
    Ingress->>Coord: remote start
    Coord-->>Laptop: recording and request id
    loop Audio chunks
        Laptop->>Coord: audio for owning request
    end
    Laptop->>Coord: finish
    Coord-->>Laptop: processing
    loop Poll
        Laptop->>Coord: status
    end
    Coord-->>Laptop: ready
    Laptop->>Coord: one commit
    Coord->>Delivery: consume and deliver or prepare plan
    Delivery-->>Laptop: terminal response
```

*The owning connection carries audio through a staged, one-shot commit; SSH only transports bytes.*

## PTT clients

### Workstation-local bridge

`handy-ptt-bridge.py` permanently grabs **Left Control (`Control_L`)**. Existing prose that says Right Control is stale; the implementation, service description, and current behavior are Left Control. A distinct-press tracker suppresses X11 autorepeat.

Arming is a two-phase handshake: ensure the app PID/readiness file exists, signal prepare, wait for `prepared`, atomically grab Space and Delete for modifiers `0`, `LockMask`, `Mod2Mask`, and `LockMask | Mod2Mask`, signal mode-on, then require `armed`. X errors are asynchronous: `grab_keys` supplies an `onerror` recorder to every passive grab and calls `dpy.sync()`; any recorded or synchronous `XError` causes every attempted key/modifier pair to be ungrabbed followed by another sync. Any grab/readiness failure sends mode-off and releases grabs. While off, Space/Delete are ungrabbed and pass through normally. In armed mode Space starts/stops local recording, Delete cancels locally owned recording or processing, and Left Control exits; Space cannot invert processing or affect remote work. PID replacement, readiness loss, shutdown, or bridge restart resets trackers, releases grabs, and never signals a replacement process as though it owned the old handshake. See [Dictation pipeline](dictation-pipeline.md) for the app-side prepared/armed signal map and `signal_handle::setup_signal_handler` dispatch.

### Remote laptop client

The user service starts in `off`; Left Control arms and dynamically grabs Space/Delete. Space starts or finishes; Delete cancels; Left Control exits. States are `off`, `armed`, `recording`, `processing`, and `failed`; notifications are advisory. Capture configuration must invoke an absolute `pw-record`, exactly declare 16 kHz, mono, signed-16 format, and stream to stdout. SSH host/helper strings are allow-listed, executable paths are absolute, and config rejects unknown or mode-incompatible fields.

```mermaid
stateDiagram-v2
    [*] --> Off
    Off --> Armed: Left Control and grabs succeed
    Armed --> Recording: Space and start accepted
    Recording --> Processing: Space and finish accepted
    Recording --> Armed: Delete and cancelled
    Processing --> Armed: cancelled or terminal success
    Processing --> Failed: protocol or delivery failure
    Failed --> Armed: Left Control rearm succeeds
    Armed --> Off: Left Control
    Failed --> Off: shutdown
```

*The laptop remains armed across requests, but every protocol or effect ambiguity fails the current request closed.*

## Target semantics and delivery

### Local workstation Herdr

When Herdr binding is enabled, `begin_capture` mints a recording token and asynchronously captures the exact focused pane only if the active X11 window title is `herdr`. `CaptureOutcome` is `Legacy`, `Bound(pane_id)`, or `Failed(reason)`; up to eight token results are retained. Stop consumes the token associated with that recording. A bound or failed capture **never falls back** to OS input. Bound delivery uses exactly that start-selected pane, collapses CR/LF to spaces, and optionally includes one trailing carriage return in the same `herdr pane send-text` call.

### Remote Herdr

Remote start captures no pane. It captures an immutable `HerdrSessionIdentity`: status version/protocol/session plus absolute owned socket path, device/inode/UID, peer PID/UID/GID, and `/proc` process start time. At commit, the laptop first requires the same start-time Ghostty window ID/PID/title/class to remain active. The workstation re-observes exact Herdr identity; mismatch fails before an effect. Only then does it take one bounded snapshot, require one unique valid focused pane (`w…:p…`, ASCII, max 64 bytes), freeze it, and issue at most one literal send. Remote Herdr therefore follows **commit-time session focus**, unlike workstation-local start-bound targeting.

### Laptop-local injection

Local mode does not inspect Herdr and saves no start target. Immediately before commit and again before injection, the laptop requires one readable X11 focus. Commit consumes the ready text and returns the bounded plan. The client releases its dynamic Space/Delete grabs, marks the effect attempted, invokes absolute `xdotool` once with `type --delay 0 --clearmodifiers -- TEXT`, then at most one submit-key invocation, and reacquires grabs before returning to armed. The recipient is whichever window is focused at effect time. A failed second focus check injects nothing after consuming the handoff; adapter failure, submit failure, or grab-reacquisition failure enters visible failed state and never retries or falls back. Blank successful output never commits; oversized output cannot form a plan and fails before injection. Synthetic typing, Unicode, layouts, IMEs, and application acceptance remain best-effort.

### Ordinary local delivery

Without a bound Herdr outcome, `clipboard::paste` applies optional trailing space, then routes by `PasteMethod`: `direct`, `ctrl_v`, `ctrl_shift_v`, `shift_insert`, `external_script`, or `none`. Direct Linux typing selects configured `TypingTool` (`auto`, `wtype`, `kwtype`, `dotool`, `ydotool`, `xdotool`) before Enigo fallback.

Clipboard paste first snapshots existing text, or an image when no text exists. On Wayland it prefers external `wl-copy` when available; otherwise it uses the Tauri clipboard plugin. After the configured pre-paste delay and key chord, it waits the post-paste delay, restores prior text/image, or clears the temporary transcript if the clipboard was initially empty. Initial write/tool failures propagate; restoration is best-effort and does not select another target. External script receives the text and must be configured. Auto-submit runs only when delivery is not `none`; optional clipboard copy occurs after successful delivery. There is no target-changing fallback after a Herdr decision.

## Security, invariants, and failures

- `$XDG_RUNTIME_DIR` must be an app-user-owned directory. `qq-dictation/` is mode `0700`; `remote.sock` is `0600`, owned by the app user, and peers must match via `SO_PEERCRED`. Stale sockets are removed only after type/owner/connect and inode rechecks; shutdown removes only the originally bound inode.
- SSH is the remote authentication boundary. The helper validates the owner-only socket but adds no credential or policy. Compromise of the same Unix user, SSH account, X11 session, configured tools, or Herdr process is outside this boundary.
- The coordinator is the sole operation authority: remote cannot overlap local work, and disconnect/cancel/lifetime cleanup retires ownership.
- Ready text is not observable before consumption. Commit and local injection are never retried after the irrevocable/effectful boundary; response loss or tool timeout can be effect-uncertain but cannot duplicate text.
- Herdr subprocesses have hard timeouts. Identity, snapshot, focus, protocol, and tool failures terminate delivery rather than choosing another target.
- Live SSH, PipeWire, X11 grabs/focus, Ghostty/Herdr behavior, systemd user sessions, and application rendering are integration assumptions, not established by unit tests.

## Installation and change surfaces

`install-remote-workstation.py` atomically installs only the mode-0755 SSH stream helper under an operator-owned home, rejects foreign/non-regular destinations, and preserves timestamped backups; it intentionally does not modify Herdr or SSH configuration. `install-remote-laptop.py` validates a complete Herdr or local config, creates mode-0600 config under a mode-0700 directory, refuses silent config overwrite, installs the mode-0755 client and mode-0644 `handy-remote-client.service`, then daemon-reloads, enables, restarts, and verifies a stable running PID. Both installers use atomic replacement and reject unsafe ownership/type.

The local installer installs `handy-ptt-bridge.py` and `handy-ptt.service`; both PTT and remote-client user services use `DISPLAY=:0`, restart on failure after one second, and are wanted by `default.target`. Operational rollback and the fact that multi-file/service installation is not one transaction are covered in [Build, install, and Check](../operations/build-install-check.md).

When changing this boundary:

- Protocol: update Rust request/response serde types and bounds, Python encoder/decoder/state transitions, `docs/remote-dictation-protocol.md`, and both Rust/Python negative tests; preserve strict ownership and no-retry semantics or version the protocol.
- Delivery mode or injection plan: update `RemoteDeliveryMode`, coordinator staging/commit, `RemoteInjectionPlan`, laptop config validation/injector, installer arguments, generated operational docs, and tests.
- Herdr identity/pane schema: update status/snapshot parsers, equality and bounds tests, and local-versus-remote targeting tests; do not merge the two capture models.
- PTT key/handshake: update both Python clients, service descriptions, app signals/readiness contract, installers, and tests. `Control_L` is current.
- Local paste method/tool: update settings enums/defaults, `clipboard.rs` routing, generated bindings and settings UI described in [Frontend and IPC](../frontend/app-and-api.md).

## Focused tests and narrow validation

```bash
cargo test --manifest-path src-tauri/Cargo.toml remote::tests
cargo test --manifest-path src-tauri/Cargo.toml target_binding::tests
cargo test --manifest-path src-tauri/Cargo.toml clipboard::tests
python3 -m unittest tests.test_handy_ptt_bridge
python3 -m unittest tests.test_remote_helpers
python3 -m unittest tests.test_remote_laptop_client
python3 -m unittest tests.test_remote_installers
```

Named contract tests include coordinator `remote_audio_chunks_and_total_are_bounded_without_truncation`, `terminal_status_is_bound_to_connection_and_expires`, and `ready_retains_exclusive_processing_ownership_until_one_commit_attempt`; ingress `read_timeout_is_tolerated_only_between_idle_session_frames` and `local_injection_is_framed_only_on_one_consuming_owner_response`; and Python `test_start_audio_finish_ready_commit_and_terminal_states_are_ordered`, `test_local_focus_refusal_before_or_after_handoff_never_injects`, `test_local_adapter_failure_is_one_marked_attempt_without_retry_or_fallback`, `test_local_reacquisition_failure_after_injection_fails_without_repeat`, and `test_window_mismatch_at_ready_sends_no_commit_and_releases_resources`. PTT changes must also update `ReadyTests.test_marker_must_name_the_current_process`, `SignalConstantTests.test_mode_signals_are_distinct_realtime_signals_within_bounds`, `GrabTests`, and coordinator `mode_space_and_delete_apply_only_to_local_owner`.

Together these cover strict framing/version/bounds, socket ownership and replacement, connection/request ownership, terminal expiry, coordinator lifecycle, pane/identity validation, one-shot commit/injection, X11 state machines and grabs, short writes, config constraints, atomic installs, and service health. For a protocol-only change, start with the remote Rust filter plus `test_remote_helpers` and `test_remote_laptop_client`; add installer tests only when installed files/config/services change. Do not claim end-to-end delivery until manually proven with real SSH, X11, PipeWire, Herdr/Ghostty, and the target application.
