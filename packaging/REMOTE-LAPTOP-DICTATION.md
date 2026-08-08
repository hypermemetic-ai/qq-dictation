# Remote Linux/X11 laptop dictation

This workflow keeps transcription authority on the workstation. The laptop captures 16 kHz mono signed 16-bit little-endian PCM, controls one request over one ordinary authenticated SSH connection, and displays local state. It installs no ASR library or model. The workstation retains VAD, streaming ASR, selected language/model/custom words, second-pass settings, history, cancellation, exact-pane delivery, and auto-submit.

## Workstation install

The normal per-user workstation installer now installs these stable executables:

- `~/.local/bin/handy-remote-stream.py`, the SSH stdio-to-app-socket byte bridge;
- `~/.local/bin/handy-remote-bind.py`, the detached exact-pane claimant.

It also runs `scripts/configure-remote-herdr.py` against the active Herdr config. The utility supports Herdr 0.7.5 only. It appends or updates one marked detached-shell `prefix+alt+d` command, validates a same-directory candidate through `HERDR_CONFIG_PATH`, atomically replaces only the resolved regular target, reloads, and checks the running server. A config symlink remains the same symlink. The resolved target is backed up with a `.before-qq-dictation.*` suffix. Missing, foreign-owned, world-writable, chained-symlink, hard-linked, malformed, duplicate-marker, occupied-chord, and conflicting-command states are refused. Reload or post-check failure atomically restores and reloads the backup.

The standalone hermetic/install surface is:

```bash
/usr/bin/python3 scripts/install-remote-workstation.py
```

Do not run either installer until the reviewed Change reaches the approved live-install step.

## Laptop requirements and install

Supported scope is one Linux/X11 laptop running Ghostty, ordinary SSH, and workstation Herdr. Required tools are `/usr/bin/python3` with `python-xlib`, `ssh`, `xdotool`, `notify-send`, and PipeWire's `pw-record`. The configured SSH alias uses the operator's existing SSH authentication; no password, key, token, or secret command is stored by qq-dictation.

Install with exact facts from that laptop, for example:

```bash
/usr/bin/python3 scripts/install-remote-laptop.py \
  --ssh-host WORKSTATION_SSH_ALIAS \
  --ghostty-title EXACT_ACTIVE_GHOSTTY_TITLE \
  --ghostty-class EXACT_GHOSTTY_WM_CLASS \
  --herdr-prefix ctrl+b
```

The placeholders are not source defaults. The installer writes owner-only `~/.config/qq-dictation/remote-laptop.json`, installs `~/.local/bin/handy-remote-client.py` and its user service, and starts the service in mode off. Existing differing configuration is never overwritten. The microphone command is a JSON argv array, defaults to:

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

A PipeWire `--target` may be added to that argv during installation. The client validates the exact `pw-record` sample contract and executes every local process without a shell. The remote helper value is restricted to a shell-safe executable path because OpenSSH necessarily passes the remote command to the remote login shell.

## Controls and target handshake

- Right-Control arms or exits remote mode.
- Space starts or finishes a recording.
- Delete cancels recording or processing while remaining armed.

Arming creates exactly one SSH helper process and dynamically grabs Space/Delete. Start captures the active X11 window ID and requires its exact configured title and class. After the workstation returns `pending`, the client rechecks that same active window and asks `xdotool` to inject the configured Herdr prefix followed by the reserved `alt+d` into that exact window ID. It rechecks the active identity, waits for `bound`, and starts `pw-record` only after the bound response. It never reads Herdr global focus and never opens a second SSH connection.

PCM is framed and sent as it arrives, before Space-stop. Stop terminates and reaps capture, sends `finish`, and shows `processing` until the workstation reports a terminal result. Notifications expose `off`, `armed`, `recording`, `processing`, and `failed` states.

Window mismatch, chord failure, missing/late binding, malformed/stale response, SSH EOF/timeout/replacement, microphone truncation/exit, app/Herdr loss, and unexpected state all fail closed. Failure cancels the owned request when possible, terminates and reaps both children, releases Space/Delete, and leaves a visible failed non-recording state. No automatic reconnect can attach an orphan request to later delivery.

## Approved later live proof

After source review and owner Checks, batch these operator-only steps into one session:

1. Record the existing laptop's local stop-to-visible-text baseline.
2. Confirm the laptop's real SSH alias, exact active Ghostty title/class, Herdr prefix, PipeWire target/argv, and that `prefix+alt+d` has no operator collision.
3. Run the workstation and laptop per-user installers; record the Herdr target path, pre-install hash/owner/mode, backup, unchanged symlink identity, helper/service status, and healthy Herdr reload check.
4. Arm in the intended Ghostty/SSH/Herdr client and perform one normal remote dictation with the configured workstation second pass.
5. After target binding, move Herdr focus to another pane before completion. Verify text and Enter reach only the originally bound pane.
6. Record one SSH helper, PCM frames arriving before stop, workstation streaming/process timing, stop-to-delivery time lower than the captured local baseline, and the resulting history row's destination/second-pass metadata.
7. Exercise Delete and Right-Control cancellation and one helper-loss case; verify mode/status, released grabs, and no delayed delivery.

No paid provider request, live config edit, service restart, laptop connection, or history mutation belongs in source-level Checks.
