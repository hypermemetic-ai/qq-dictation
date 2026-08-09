# Remote Linux/X11 laptop dictation

This supported path is Ghostty -> ordinary authenticated SSH -> the full normal workstation Herdr application. The laptop captures 16 kHz mono signed 16-bit little-endian PCM, controls one request over one SSH helper connection while armed, and displays local state. It installs no ASR runtime or model. The workstation remains the only VAD, streaming model/language/custom-word, second-pass, history/WAV-retention, cancellation, delivery, and auto-submit authority.

## Target convention

For this laptop-over-SSH path only, keep the intended Herdr pane session-globally selected until text arrives. No pane is captured at Space-start. Instead, workstation start records the configured/default live Herdr server/session identity from public status, owned Unix-socket metadata, Linux peer credentials, and peer process start time without reading focus or layout. When processing is ready, the laptop automatically verifies the original Ghostty window is still active and unchanged, then commits. The workstation first requires the same exact server/session identity, then selects one live pane from one Herdr session snapshot and sends explicitly only to it.

Another Herdr client changing session-global focus before commit can redirect this remote result. This path does **not** promise the pane selected at Space-start or safety under intervening focus changes. Workstation-local dictation remains different: it retains its independently captured exact target and never uses this delivery-time selector.

There is no Herdr binder, reserved chord, config edit/reload, custom command, direct terminal attach, popup, proxy, or fork. The workstation installer does not inspect or modify Herdr config or a config symlink/target.

## Workstation install

The normal local installer installs only:

- `~/.local/bin/handy-remote-stream.py`, the SSH stdio-to-app-socket byte bridge.

The standalone hermetic surface is:

```bash
/usr/bin/python3 scripts/install-remote-workstation.py
```

Do not run installers until the reviewed Change reaches its approved live-install step.

## Laptop requirements and install

Scope is one Linux/X11 laptop running Ghostty, ordinary SSH, and workstation Herdr. Required tools are `/usr/bin/python3` with `python-xlib`, `ssh`, `notify-send`, and PipeWire's `pw-record`. The configured SSH alias uses the operator's existing authentication; no password, key, token, or secret command is stored by qq-dictation.

The remote client is the X11 session's sole Right-Control owner from service start, including while remote mode is off. Any other application that owns the Right-Control grab must already be disabled before installation or service start. The installer and client do not discover, stop, disable, remember, restore, or coexist with a previous owner. There is no alternate remote mode key and no managed local/remote handoff or rollback.

Install with exact facts from that laptop:

```bash
/usr/bin/python3 scripts/install-remote-laptop.py \
  --ssh-host WORKSTATION_SSH_ALIAS \
  --ghostty-title EXACT_ACTIVE_GHOSTTY_TITLE \
  --ghostty-class EXACT_GHOSTTY_WM_CLASS
```

The placeholders are not source defaults. The installer writes mode `0600` `~/.config/qq-dictation/remote-laptop.json`, installs `~/.local/bin/handy-remote-client.py` and its user service, and starts the service in mode off. An existing config must be an operator-owned regular file with exact mode `0600`; differing or less-private configuration is refused before client/service replacement or systemd.

Installation reports success only after the service is active and running with one positive, unchanged main PID and an unchanged restart count at both ends of a fixed observation interval longer than its one-second restart delay. This health gate runs on first install and every idempotent rerun. Startup failure, a conflicting X11 grab owner, a crash/restart, or malformed or uncertain systemd state makes installation fail nonzero and prints a bounded `handy-remote-client.service` status diagnostic instead of the success line.

The microphone command is a JSON argv array and defaults to:

```json
[
  "/usr/bin/pw-record",
  "--rate",
  "16000",
  "--channels",
  "1",
  "--format",
  "s16",
  "-"
]
```

A PipeWire `--target` may be added during installation. The client validates this sample contract and executes local processes without a shell. The remote helper is restricted to a shell-safe executable path because OpenSSH passes the remote command to the login shell.

## Controls and lifecycle

- Right-Control arms or exits remote mode.
- Space starts or finishes recording.
- Delete cancels recording or processing while remaining armed.

Arming creates one SSH helper and dynamically grabs Space/Delete. Space-start captures the active configured Ghostty window's exact X11 ID, PID, title, and class. The workstation independently captures the exact live Herdr server/session identity without capturing a pane. A valid workstation `start` immediately returns `recording`; capture begins and PCM streams before Space-stop. Stop terminates/reaps capture, sends `finish`, and shows `processing`—never recording—until a terminal result.

For nonblank output, the workstation reports `ready`. With no new physical gesture, the laptop requires the original exact window to still exist and be active, then attempts one matching commit. The serialized workstation commit revalidates the start-owned Herdr server/session identity before reading focus or sending; a mismatch terminally fails with no snapshot, send, or fallback. Exact identity equality begins the irrevocable snapshot/send boundary, so a commit whose response is lost is never retried because delivery may already have happened.

Recording cancellation returns to armed immediately. Processing cancellation remains visibly non-recording and busy until workstation completion is terminal. Terminal requests retire so another Space can start on the same helper. Notifications expose only `off`, `armed`, `recording`, `processing`, and `failed`.

SSH/helper, capture, or app replacement; malformed/stale responses; timeout; and unexpected state all fail closed. Herdr server/session replacement is fenced separately from the laptop window: public status, owned socket path/device/inode, peer PID/credentials, and peer process start identity must match from workstation start to serialized commit even if Ghostty's X11 identity remains unchanged. Failure before matching identity validation cancels or terminally fails the owned request with no focus snapshot or send, reaps children, releases Space/Delete, and leaves a visible non-recording state. No reconnect can attach an orphan request to later delivery.

## Approved later live proof

After source review and owner Checks:

1. Capture the laptop's existing local stop-to-visible-text baseline.
2. Confirm its real SSH alias, exact active Ghostty title/class/PID behavior, and PipeWire target/argv without recording secrets.
3. Install workstation and laptop per-user surfaces and verify one helper, mode `0600`, and unchanged Herdr config/symlink/target.
4. Keep the intended session-global Herdr pane selected through one normal remote dictation using the configured workstation second pass.
5. Record PCM arriving before stop, workstation processing timing, lower stop-to-delivery delay, exact selected destination, and resulting history/second-pass metadata.
6. Exercise Delete, Right-Control, window mismatch, and helper loss before commit; verify truthful state, released grabs, immediate temporary-audio policy, and no delayed text/Enter.

No paid provider request, live config edit, service restart, laptop connection, or live history/settings mutation belongs in source-level Checks.
