# Remote Linux/X11 laptop dictation

This supported path is one configured Linux/X11 laptop -> ordinary authenticated SSH -> the workstation qq-dictation application. The laptop captures 16 kHz mono signed 16-bit little-endian PCM, controls one request over one SSH helper connection while armed, and displays local state. It installs no ASR runtime or model.

The workstation remains the sole authority for VAD, streaming, model/language/custom words, the configured second pass, output processing, trailing space, auto-submit key, history/WAV retention, and cancellation. A per-install `delivery_mode` selects where finished nonblank text goes:

- `herdr`: preserve the existing remote path and deliver to the workstation Herdr pane selected at commit.
- `local`: return one bounded final injection plan to the laptop and type it into the window holding X11 focus at delivery.

There is no per-request destination picker and no second transcription path.

## Target conventions

### Herdr mode

Keep the intended Herdr pane session-globally selected until text arrives. No pane is captured at Space-start. Workstation start records the configured/default live Herdr server/session identity from public status, owned Unix-socket metadata, Linux peer credentials, and peer process start time without reading focus or layout. When processing is ready, the laptop automatically verifies the Ghostty window captured at Space-start is still active and unchanged, then commits. The workstation first requires the same exact server/session identity, then selects one live pane from one Herdr session snapshot and sends explicitly only to it.

Another Herdr client changing session-global focus before commit can redirect this result. Herdr mode does **not** promise the pane selected at Space-start or safety under intervening Herdr focus changes.

### Local mode

No laptop or workstation target is captured when Space starts recording. When processing reports `ready`, the laptop requires a readable current X11 focus before requesting the one consuming result. After the result arrives, it checks readable focus again immediately before synthetic typing. Neither check saves or activates a target, and the two checks need not name the same window. The target is whichever laptop window receives X11 input when injection runs.

Keep the intended laptop window focused until the text arrives. This is an operator convention, not a start-target guarantee. Missing, malformed, or unreadable focus refuses delivery. There is no fallback to Ghostty, Herdr, a prior window, the clipboard, or another application.

Workstation-local dictation remains different and unchanged: it retains its independently captured exact target and never uses either remote delivery-time convention.

## Workstation install

The normal local installer installs only:

- `~/.local/bin/handy-remote-stream.py`, the SSH stdio-to-app-socket byte bridge.

The standalone hermetic surface is:

```bash
/usr/bin/python3 scripts/install-remote-workstation.py
```

There is no Herdr binder, reserved chord, config edit/reload, custom command, direct terminal attach, popup, proxy, or fork. The workstation installer does not inspect or modify Herdr config or a config symlink/target. Do not run installers until the reviewed Change reaches its approved live-install step.

## Laptop requirements

Both modes require `/usr/bin/python3` with `python-xlib`, `ssh`, `notify-send`, and PipeWire's `pw-record`. Local mode additionally requires X11 and an executable absolute `xdotool_path` (default `/usr/bin/xdotool`). Herdr mode neither configures nor validates xdotool.

The configured SSH alias uses the operator's existing authentication; no password, key, token, or secret command is stored by qq-dictation.

The remote client is the X11 session's sole Right-Control owner from service start, including while remote mode is off. Any other application that owns the Right-Control grab must already be disabled before installation or service start. The installer and client do not discover, stop, disable, remember, restore, or coexist with a previous owner. There is no alternate remote mode key and no managed local/remote handoff or rollback.

## Per-install configuration

Legacy configuration without `delivery_mode` remains valid and means `herdr`.

Install legacy-compatible Herdr delivery with exact facts from that laptop:

```bash
/usr/bin/python3 scripts/install-remote-laptop.py \
  --ssh-host WORKSTATION_SSH_ALIAS \
  --ghostty-title EXACT_ACTIVE_GHOSTTY_TITLE \
  --ghostty-class EXACT_GHOSTTY_WM_CLASS
```

An explicit equivalent is:

```bash
/usr/bin/python3 scripts/install-remote-laptop.py \
  --ssh-host WORKSTATION_SSH_ALIAS \
  --delivery-mode herdr \
  --ghostty-title EXACT_ACTIVE_GHOSTTY_TITLE \
  --ghostty-class EXACT_GHOSTTY_WM_CLASS
```

Install laptop-local focused-window delivery with:

```bash
/usr/bin/python3 scripts/install-remote-laptop.py \
  --ssh-host WORKSTATION_SSH_ALIAS \
  --delivery-mode local \
  --xdotool-path /usr/bin/xdotool
```

The placeholders are not source defaults. Configuration is strict:

- both modes require `ssh_host` and the validated `capture_argv`;
- Herdr mode requires nonempty `ghostty_title` and `ghostty_class` and refuses `xdotool_path`;
- local mode requires `delivery_mode: "local"` and an absolute `xdotool_path`, and refuses Ghostty title/class fields;
- unknown fields and other mode values are refused.

The installer writes mode `0600` `~/.config/qq-dictation/remote-laptop.json`, installs `~/.local/bin/handy-remote-client.py` and its user service, enables the exact `handy-remote-client.service`, and unconditionally restarts it in mode off. An existing config must be an operator-owned regular file with exact mode `0600`. An idempotent rerun with no configuration arguments preserves the existing validated config; supplied configuration must equal it exactly. Differing, cross-mode, unsafe, or less-private configuration is refused before client/service replacement or systemd.

Only after restart succeeds does installation check that the service is active and running with one positive, unchanged main PID and an unchanged restart count at both ends of a fixed observation interval longer than its one-second restart delay. This health gate runs on first install and every idempotent rerun. Startup failure, a conflicting X11 grab owner, a crash/restart, or malformed or uncertain systemd state makes installation fail nonzero and prints a bounded `handy-remote-client.service` status diagnostic instead of the success line.

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

## Controls and shared lifecycle

- Right-Control arms or exits remote mode.
- Space starts or finishes recording.
- Delete cancels recording or processing while remaining armed.

Arming creates one SSH helper and dynamically grabs Space/Delete. A valid workstation `start` immediately returns `recording`; capture begins and PCM streams before Space-stop. Herdr mode captures the active configured Ghostty window's exact X11 ID, PID, title, and class before start. Local mode reads no start window and requires no Herdr state. Stop terminates/reaps capture, sends `finish`, and shows `processing`—never recording—until a terminal result.

Recording cancellation returns to armed immediately. Processing cancellation remains visibly non-recording and busy until workstation completion is terminal. Terminal requests retire so another Space can start on the same helper. Notifications expose only `off`, `armed`, `recording`, `processing`, and `failed`.

SSH/helper, capture, or app replacement; malformed/stale responses; timeout; and unexpected state all fail closed, reap children, release Space/Delete, and leave a visible non-recording state. Loss before the consuming commit response prevents local injection. No reconnect can attach an orphan request to later delivery.

## Herdr delivery sequence

For nonblank output, the workstation reports `ready`. The laptop requires the original exact Ghostty window to still exist and be active, then attempts one matching commit. The serialized workstation commit revalidates the start-owned Herdr server/session identity before reading focus or sending. A mismatch terminally fails with no snapshot, send, or fallback. Exact identity equality begins the irrevocable snapshot/send boundary, so a commit whose response is lost is never retried because delivery may already have happened.

The workstation applies trailing-space and auto-submit policy and makes at most one explicit Herdr pane-send attempt. Herdr behavior, including workstation-owned clipboard handling, is unchanged.

## Local injection sequence

For nonblank output, status reports `ready` without result text. The laptop checks readable X11 focus and sends one commit. Only that consuming response to the exact owning connection/request may include a nonempty final text/submit plan; polling, cancellation, error, Herdr, terminal replay, stale request, and another connection never receive it. The workstation has already applied output processing and the configured trailing space and selected `enter`, `ctrl_enter`, `cmd_enter`, or no key from its auto-submit settings.

The laptop validates the plan and its 8192-byte text bound, checks readable focus again, marks the attempt, and invokes the configured xdotool once for exact text. Only after reported text success does it make at most one configured submit-key attempt. It never retries text, retries the key, partially re-injects, re-requests the result, activates a saved target, or falls back. Missing focus before commit prevents result consumption; focus loss after consumption, xdotool error, timeout, or any uncertain effect becomes a visible local failure even though the consumed workstation result cannot be returned again.

Direct X11 synthetic typing is best-effort. Some applications intercept, reject, or transform synthetic input, and Unicode, keyboard-layout, compose, and IME behavior varies. An xdotool success confirms only that the configured adapter accepted its one attempt; the one-laptop proof below establishes ordinary behavior only for the tested laptop/application/version combination.

## Approved later one-laptop proof

After source review and all owner Checks, the accountable owner—not this source-level implementation run—performs the one paid live proof:

1. Confirm the one laptop's real SSH alias, X11 session, executable xdotool path, and PipeWire target/argv without recording secrets.
2. Install the reviewed workstation and local-mode laptop per-user surfaces; verify one helper, mode `0600`, stable service health/grabs, no Herdr requirement for start, and no live settings change beyond the already approved configuration.
3. Focus one ordinary non-Herdr laptop window and keep it focused through one normal remote dictation using the configured workstation second pass.
4. Record PCM arriving before stop, stop-to-result timing, client/workstation logs, exact visible injected text and destination, trailing-space/submit behavior, and the resulting history/second-pass metadata.
5. Exercise the approved hermetic failure cases separately; do not add paid requests merely to repeat focus-loss, adapter-failure, cancellation, or replay coverage.

The proof establishes only this configured Linux/X11 laptop at the tested versions. It makes no Wayland, other-OS, multi-laptop, multi-user, arbitrary-application, Unicode/IME-wide, or future-version claim.

No paid provider request, live config edit, service restart, laptop connection, credential use, or live history/settings mutation belongs in source-level Checks.
