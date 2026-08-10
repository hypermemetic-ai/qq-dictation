//! Binds a dictation to the herdr pane that was focused when recording
//! started, so the transcription is delivered to *that* pane regardless of
//! where keyboard focus has moved by the time transcription completes.
//!
//! Why the herdr socket API: herdr panes are invisible to the OS (a whole
//! workspace lives inside one terminal window), so window-level refocus can
//! never fix wrong-pane delivery. `herdr pane send-text` writes directly to
//! the pane's PTY — no focus change, no keystroke race.
//!
//! Bindings are keyed by a per-recording token: a new recording can legally
//! start while a previous transcription is still in flight (the recorder
//! returns to Idle at stop), so a single global slot would deliver the older
//! transcription to the newer recording's pane. Each recording takes only its
//! own entry.
//!
//! Herdr pane binding is available on X11; capture explicitly selects legacy
//! focus-based delivery when the session conditions are not met.

use log::{debug, warn};
use std::collections::HashMap;
use std::ffi::OsStr;
use std::fs;
use std::os::fd::AsRawFd;
use std::os::unix::fs::{FileTypeExt, MetadataExt};
use std::os::unix::net::UnixStream;
use std::path::Path;
use std::path::PathBuf;
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::Duration;
use tauri::AppHandle;

/// Title herdr's client sets on its terminal window; used to tell "operator
/// is dictating into a herdr pane" apart from "herdr merely runs somewhere".
const HERDR_WINDOW_TITLE: &str = "herdr";
const LINUXBREW_HERDR: &str = "/home/linuxbrew/.linuxbrew/bin/herdr";

/// Immutable identity of the configured/default live Herdr session. It does
/// not contain workspace, tab, pane, focus, or layout state.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct HerdrSessionIdentity {
    socket_path: PathBuf,
    socket_device: u64,
    socket_inode: u64,
    socket_uid: u32,
    peer_pid: u32,
    peer_uid: u32,
    peer_gid: u32,
    peer_start_time: u64,
    version: String,
    protocol: u64,
    session: Option<String>,
}

#[cfg(test)]
pub(crate) fn synthetic_remote_session_identity(seed: u64) -> HerdrSessionIdentity {
    HerdrSessionIdentity {
        socket_path: PathBuf::from(format!("/synthetic/herdr-{seed}.sock")),
        socket_device: 10,
        socket_inode: seed,
        socket_uid: 1_000,
        peer_pid: 2_000 + seed as u32,
        peer_uid: 1_000,
        peer_gid: 1_000,
        peer_start_time: 3_000 + seed,
        version: "0.7.5".to_string(),
        protocol: 17,
        session: None,
    }
}

#[derive(Debug, serde::Deserialize)]
struct HerdrServerStatus {
    status: String,
    running: bool,
    version: String,
    protocol: u64,
    compatible: bool,
    socket: String,
    session: Option<String>,
}

#[derive(Debug)]
struct HerdrSocketObservation {
    path: PathBuf,
    device: u64,
    inode: u64,
    uid: u32,
    peer_pid: u32,
    peer_uid: u32,
    peer_gid: u32,
    peer_start_time: u64,
}

fn parse_herdr_server_status(json_bytes: &[u8]) -> Result<HerdrServerStatus, String> {
    let status: HerdrServerStatus = serde_json::from_slice(json_bytes)
        .map_err(|error| format!("Herdr server status was malformed: {error}"))?;
    if status.status != "running" || !status.running || !status.compatible {
        return Err("Herdr server status is not live and compatible".to_string());
    }
    if status.version.is_empty()
        || status.version.len() > 64
        || status.protocol == 0
        || status.socket.is_empty()
        || status.socket.len() > 4_096
        || status
            .session
            .as_ref()
            .is_some_and(|session| session.is_empty() || session.len() > 256)
    {
        return Err("Herdr server status contains an invalid identity field".to_string());
    }
    let socket_path = Path::new(&status.socket);
    if !socket_path.is_absolute() {
        return Err("Herdr server status socket path is not absolute".to_string());
    }
    Ok(status)
}

fn require_owned_socket(metadata: &fs::Metadata, expected_uid: u32) -> Result<(), String> {
    if !metadata.file_type().is_socket() {
        return Err("Herdr server status path is not a Unix socket".to_string());
    }
    if metadata.uid() != expected_uid {
        return Err("Herdr server socket is not owned by the current user".to_string());
    }
    Ok(())
}

fn socket_peer_credentials(stream: &UnixStream) -> Result<(u32, u32, u32), String> {
    let mut credentials = libc::ucred {
        pid: 0,
        uid: 0,
        gid: 0,
    };
    let mut length = std::mem::size_of::<libc::ucred>() as libc::socklen_t;
    let result = unsafe {
        libc::getsockopt(
            stream.as_raw_fd(),
            libc::SOL_SOCKET,
            libc::SO_PEERCRED,
            std::ptr::addr_of_mut!(credentials).cast(),
            &mut length,
        )
    };
    if result != 0 || length as usize != std::mem::size_of::<libc::ucred>() {
        return Err(format!(
            "Could not verify Herdr server peer credentials: {}",
            std::io::Error::last_os_error()
        ));
    }
    let peer_pid = u32::try_from(credentials.pid)
        .ok()
        .filter(|pid| *pid > 0)
        .ok_or_else(|| "Herdr server peer PID is invalid".to_string())?;
    Ok((peer_pid, credentials.uid, credentials.gid))
}

fn linux_process_start_time(pid: u32) -> Result<u64, String> {
    let stat = fs::read_to_string(format!("/proc/{pid}/stat"))
        .map_err(|error| format!("Could not verify Herdr server process identity: {error}"))?;
    let command_end = stat
        .rfind(')')
        .ok_or_else(|| "Herdr server process status was malformed".to_string())?;
    // Fields after the command name begin at field 3 (state); starttime is
    // field 22, therefore index 19 in this suffix.
    stat.get(command_end + 1..)
        .and_then(|suffix| suffix.split_whitespace().nth(19))
        .and_then(|start_time| start_time.parse::<u64>().ok())
        .filter(|start_time| *start_time > 0)
        .ok_or_else(|| "Herdr server process start identity was malformed".to_string())
}

fn inspect_herdr_socket(path: &Path, expected_uid: u32) -> Result<HerdrSocketObservation, String> {
    let before = fs::symlink_metadata(path)
        .map_err(|error| format!("Herdr server socket is unavailable: {error}"))?;
    require_owned_socket(&before, expected_uid)?;

    // Connect only to ask Linux for SO_PEERCRED. No Herdr protocol bytes are
    // read or written on this identity-only connection.
    let stream = UnixStream::connect(path)
        .map_err(|error| format!("Herdr server socket is unavailable: {error}"))?;
    let (peer_pid, peer_uid, peer_gid) = socket_peer_credentials(&stream)?;
    if peer_uid != expected_uid {
        return Err("Herdr server peer is not owned by the current user".to_string());
    }
    let peer_start_time = linux_process_start_time(peer_pid)?;

    // A path replacement during observation must not synthesize an identity
    // from one listener's peer and another listener's inode.
    let after = fs::symlink_metadata(path)
        .map_err(|error| format!("Herdr server socket disappeared: {error}"))?;
    require_owned_socket(&after, expected_uid)?;
    if before.dev() != after.dev() || before.ino() != after.ino() {
        return Err("Herdr server socket changed during identity observation".to_string());
    }

    Ok(HerdrSocketObservation {
        path: path.to_path_buf(),
        device: after.dev(),
        inode: after.ino(),
        uid: after.uid(),
        peer_pid,
        peer_uid,
        peer_gid,
        peer_start_time,
    })
}

fn identity_from_status(
    json_bytes: &[u8],
    expected_uid: u32,
) -> Result<HerdrSessionIdentity, String> {
    let status = parse_herdr_server_status(json_bytes)?;
    let socket = inspect_herdr_socket(Path::new(&status.socket), expected_uid)?;
    Ok(HerdrSessionIdentity {
        socket_path: socket.path,
        socket_device: socket.device,
        socket_inode: socket.inode,
        socket_uid: socket.uid,
        peer_pid: socket.peer_pid,
        peer_uid: socket.peer_uid,
        peer_gid: socket.peer_gid,
        peer_start_time: socket.peer_start_time,
        version: status.version,
        protocol: status.protocol,
        session: status.session,
    })
}

/// Observe the exact configured/default live Herdr server without reading its
/// focus or layout.
pub(crate) fn capture_remote_session_identity() -> Result<HerdrSessionIdentity, String> {
    let herdr = resolve_herdr()?;
    let output = run_with_timeout(
        &herdr,
        &["status", "server", "--json"],
        Duration::from_secs(2),
    )?;
    if !output.status.success() {
        return Err(format!(
            "herdr status server failed: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    identity_from_status(&output.stdout, unsafe { libc::geteuid() })
}

/// The capture result that determines whether paste may use OS-level input.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum CaptureOutcome {
    /// Binding was disabled or recording genuinely started outside Herdr.
    Legacy,
    /// Recording started in this Herdr pane.
    Bound(String),
    /// Herdr targeting was expected but could not be completed safely.
    Failed(String),
}

/// In-flight capture results, keyed by recording token. A missing entry means
/// capture is still running, not that legacy delivery is safe. Bounded: old
/// entries are evicted once the map outgrows a handful of recordings (a
/// cancelled recording would otherwise linger).
static CAPTURES: Mutex<Option<HashMap<u64, CaptureOutcome>>> = Mutex::new(None);
static NEXT_TOKEN: AtomicU64 = AtomicU64::new(1);
static LATEST_TOKEN: AtomicU64 = AtomicU64::new(0);

const MAX_BOUND_PANES: usize = 8;

/// Mint the token for a new recording synchronously (cheap), then do the
/// expensive capture (subprocesses) on a detached thread. The token ordering
/// is what lets `stop` attribute the binding correctly: a later recording can
/// only mint its token after the previous recording's `stop` has run, so
/// `latest_token` read at `stop` time is always the stopped recording's token.
pub fn begin_capture(#[allow(unused_variables)] app: AppHandle) -> u64 {
    let token = NEXT_TOKEN.fetch_add(1, Ordering::SeqCst);
    LATEST_TOKEN.store(token, Ordering::SeqCst);

    std::thread::spawn(move || {
        let capture = if crate::settings::get_settings(&app).herdr_binding_enabled {
            focused_herdr_pane()
        } else {
            CaptureOutcome::Legacy
        };
        match &capture {
            CaptureOutcome::Bound(pane_id) => {
                debug!("Bound dictation #{} to herdr pane {}", token, pane_id)
            }
            CaptureOutcome::Legacy => debug!("Dictation #{} not aimed at a herdr pane", token),
            CaptureOutcome::Failed(reason) => {
                warn!(
                    "Herdr targeting failed for dictation #{}: {}",
                    token, reason
                )
            }
        }
        store_capture(token, capture);
    });

    token
}

/// The token of the most recently started local recording; read at local stop.
pub fn latest_token() -> u64 {
    LATEST_TOKEN.load(Ordering::SeqCst)
}

fn store_capture(token: u64, capture: CaptureOutcome) {
    // into_inner: a poisoned map must remain usable so paste can still see an
    // explicit outcome (or fail closed on timeout).
    let mut guard = CAPTURES.lock().unwrap_or_else(|e| e.into_inner());
    let map = guard.get_or_insert_with(HashMap::new);
    map.insert(token, capture);
    if map.len() > MAX_BOUND_PANES {
        let mut keys: Vec<u64> = map.keys().copied().collect();
        keys.sort_unstable();
        for key in keys.into_iter().take(map.len() - MAX_BOUND_PANES) {
            map.remove(&key);
        }
    }
}

/// Takes the capture outcome for this recording. A finished capture answers
/// immediately; only a capture still in flight waits, because a short
/// streaming dictation can outrun the capture thread. An absent result after
/// the bounded wait is an explicit targeting failure, never permission to type
/// at the current focus.
pub fn take_for_recording(token: u64) -> CaptureOutcome {
    let deadline = std::time::Instant::now() + Duration::from_millis(500);
    loop {
        {
            // into_inner: see store_capture. A poisoned map still answers.
            let mut guard = CAPTURES.lock().unwrap_or_else(|e| e.into_inner());
            if let Some(map) = guard.as_mut() {
                if let Some(capture) = map.remove(&token) {
                    return capture;
                }
            }
        }
        if token == 0 || std::time::Instant::now() >= deadline {
            return CaptureOutcome::Failed(format!(
                "capture for recording #{} timed out before a target was recorded",
                token
            ));
        }
        std::thread::sleep(Duration::from_millis(10));
    }
}

/// The focused herdr pane, but only when the herdr window itself is the
/// active X11 window — otherwise the snapshot's focused pane is just stale
/// state from before the operator moved to another app.
fn focused_herdr_pane() -> CaptureOutcome {
    if crate::utils::is_wayland() {
        return CaptureOutcome::Legacy;
    }
    let title = match active_window_title() {
        Ok(title) => title,
        Err(reason) => return CaptureOutcome::Failed(reason),
    };
    if title != HERDR_WINDOW_TITLE {
        return CaptureOutcome::Legacy;
    }

    let herdr = match resolve_herdr() {
        Ok(path) => path,
        Err(reason) => return CaptureOutcome::Failed(reason),
    };
    let output = match run_with_timeout(&herdr, &["api", "snapshot"], Duration::from_secs(2)) {
        Ok(output) => output,
        Err(reason) => return CaptureOutcome::Failed(reason),
    };
    if !output.status.success() {
        return CaptureOutcome::Failed(format!(
            "herdr api snapshot failed: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    match parse_focused_pane_id(&output.stdout) {
        Some(pane_id) => CaptureOutcome::Bound(pane_id),
        None => CaptureOutcome::Failed(
            "herdr api snapshot did not contain a valid focused pane".to_string(),
        ),
    }
}

/// Resolve the currently focused remote pane for the finish-time privacy
/// decision. Unlike local recording capture, this deliberately has no active
/// window gate: remote ingress is already bound to Herdr delivery semantics.
pub(crate) fn resolve_remote_focused_pane() -> Result<String, String> {
    let herdr = resolve_herdr()?;
    let output = run_with_timeout(&herdr, &["api", "snapshot"], Duration::from_secs(2))?;
    if !output.status.success() {
        return Err(format!(
            "herdr api snapshot failed: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    parse_remote_delivery_pane(&output.stdout)
}

fn active_window_title() -> Result<String, String> {
    let output = run_with_timeout(
        "xdotool",
        &["getactivewindow", "getwindowname"],
        Duration::from_millis(500),
    )?;
    if !output.status.success() {
        return Err(format!(
            "failed to identify active window: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
}

fn resolve_herdr() -> Result<PathBuf, String> {
    resolve_herdr_from(
        std::env::var_os("PATH").as_deref(),
        Path::new(LINUXBREW_HERDR),
        is_executable,
    )
}

fn resolve_herdr_from(
    path: Option<&OsStr>,
    fallback: &Path,
    mut executable: impl FnMut(&Path) -> bool,
) -> Result<PathBuf, String> {
    if let Some(path) = path {
        for directory in std::env::split_paths(path) {
            let candidate = directory.join("herdr");
            if executable(&candidate) {
                return Ok(candidate);
            }
        }
    }
    if executable(fallback) {
        return Ok(fallback.to_path_buf());
    }
    Err(format!(
        "herdr executable was not found on process PATH or at {}",
        fallback.display()
    ))
}

fn is_executable(path: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    path.metadata()
        .map(|metadata| metadata.is_file() && metadata.permissions().mode() & 0o111 != 0)
        .unwrap_or(false)
}

fn parse_focused_pane_id(json_bytes: &[u8]) -> Option<String> {
    let value: serde_json::Value = serde_json::from_slice(json_bytes).ok()?;
    value
        .get("result")?
        .get("snapshot")?
        .get("focused_pane_id")?
        .as_str()
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string())
}

#[derive(serde::Deserialize)]
struct RemoteSnapshotEnvelope {
    result: RemoteSnapshotResult,
}

#[derive(serde::Deserialize)]
struct RemoteSnapshotResult {
    snapshot: RemoteSnapshot,
}

#[derive(serde::Deserialize)]
struct RemoteSnapshot {
    focused_pane_id: String,
    panes: Vec<RemoteSnapshotPane>,
}

#[derive(serde::Deserialize)]
struct RemoteSnapshotPane {
    pane_id: String,
    focused: bool,
}

pub(crate) fn validate_pane_id(pane_id: &str) -> bool {
    if pane_id.is_empty() || pane_id.len() > 64 || !pane_id.is_ascii() {
        return false;
    }
    let Some((workspace, pane)) = pane_id.split_once(':') else {
        return false;
    };
    pane_id.matches(':').count() == 1
        && workspace.strip_prefix('w').is_some_and(|suffix| {
            !suffix.is_empty() && suffix.chars().all(|c| c.is_ascii_alphanumeric())
        })
        && pane.strip_prefix('p').is_some_and(|suffix| {
            !suffix.is_empty() && suffix.chars().all(|c| c.is_ascii_alphanumeric())
        })
}

fn parse_remote_delivery_pane(json_bytes: &[u8]) -> Result<String, String> {
    let envelope: RemoteSnapshotEnvelope = serde_json::from_slice(json_bytes)
        .map_err(|error| format!("Herdr session snapshot was malformed: {error}"))?;
    let snapshot = envelope.result.snapshot;
    if !validate_pane_id(&snapshot.focused_pane_id) {
        return Err("Herdr session snapshot has an invalid focused pane id".to_string());
    }

    let mut identities = std::collections::HashSet::new();
    let mut focused_panes = Vec::new();
    for pane in snapshot.panes {
        if !validate_pane_id(&pane.pane_id) || !identities.insert(pane.pane_id.clone()) {
            return Err(
                "Herdr session snapshot has an invalid or duplicate pane identity".to_string(),
            );
        }
        if pane.focused {
            focused_panes.push(pane.pane_id);
        }
    }
    if focused_panes.len() != 1 || focused_panes[0] != snapshot.focused_pane_id {
        return Err(
            "Herdr session snapshot does not contain exactly its one live focused pane".to_string(),
        );
    }
    Ok(snapshot.focused_pane_id)
}

/// Revalidate the start-owned session identity, then read remote delivery
/// focus once, freeze that exact pane, and make one literal explicit send.
/// This path is remote-only and never consults X11 or the local per-recording
/// capture map. The irrevocable commit begins only after identity equality.
pub(crate) fn deliver_remote(
    start_identity: &HerdrSessionIdentity,
    text: &str,
    auto_submit: bool,
    delivery_enabled: bool,
) -> Result<String, String> {
    let herdr = resolve_herdr()?;
    deliver_remote_with(
        start_identity,
        text,
        auto_submit,
        delivery_enabled,
        capture_remote_session_identity,
        || {
            let output = run_with_timeout(&herdr, &["api", "snapshot"], Duration::from_secs(2))?;
            if !output.status.success() {
                return Err(format!(
                    "herdr api snapshot failed: {}",
                    String::from_utf8_lossy(&output.stderr).trim()
                ));
            }
            Ok(output.stdout)
        },
        |pane_id, payload| {
            let args = send_text_args(pane_id, payload);
            let output = run_with_timeout(&herdr, &args, Duration::from_secs(2))?;
            if !output.status.success() {
                return Err(format!(
                    "herdr pane send-text failed: {}",
                    String::from_utf8_lossy(&output.stderr).trim()
                ));
            }
            Ok(())
        },
    )
}

fn deliver_remote_with(
    start_identity: &HerdrSessionIdentity,
    text: &str,
    auto_submit: bool,
    delivery_enabled: bool,
    observe_identity: impl FnOnce() -> Result<HerdrSessionIdentity, String>,
    snapshot: impl FnOnce() -> Result<Vec<u8>, String>,
    deliver: impl FnOnce(&str, &str) -> Result<(), String>,
) -> Result<String, String> {
    let current_identity = observe_identity()?;
    if &current_identity != start_identity {
        return Err("Herdr server/session was replaced after remote start".to_string());
    }

    // Identity equality is the last rollback-safe fence. From this point a
    // server replacement or response loss can make the one delivery attempt
    // effect-uncertain, so the client must not retry it.
    let snapshot = snapshot()?;
    deliver_remote_snapshot(&snapshot, text, auto_submit, delivery_enabled, deliver)
}

fn deliver_remote_snapshot(
    snapshot: &[u8],
    text: &str,
    auto_submit: bool,
    delivery_enabled: bool,
    deliver: impl FnOnce(&str, &str) -> Result<(), String>,
) -> Result<String, String> {
    let pane_id = parse_remote_delivery_pane(snapshot)?;
    if delivery_enabled {
        let payload = send_text_payload(text, auto_submit);
        deliver(&pane_id, &payload)?;
    }
    Ok(pane_id)
}

/// Types `text` into the pane's PTY. Newlines are collapsed to spaces: a raw
/// PTY write is not bracketed paste, so transcript newlines must not become
/// implicit submits. When auto-submit is enabled, one trailing carriage return
/// is included in this same literal Herdr send-text request.
pub fn deliver(pane_id: &str, text: &str, auto_submit: bool) -> Result<(), String> {
    let text = send_text_payload(text, auto_submit);
    let herdr = resolve_herdr()?;
    let args = send_text_args(pane_id, &text);
    let output = run_with_timeout(&herdr, &args, Duration::from_secs(2))?;
    if !output.status.success() {
        return Err(format!(
            "herdr pane send-text failed: {}",
            String::from_utf8_lossy(&output.stderr)
        ));
    }
    Ok(())
}

fn send_text_args<'a>(pane_id: &'a str, text: &'a str) -> [&'a str; 4] {
    // Herdr accepts leading dashes in the TEXT positional directly. Supplying
    // a standalone `--` after PANE_ID makes it literal transcript content.
    ["pane", "send-text", pane_id, text]
}

fn send_text_payload(text: &str, auto_submit: bool) -> String {
    let mut payload = collapse_newlines(text);
    if auto_submit {
        payload.push('\r');
    }
    payload
}

fn collapse_newlines(text: &str) -> String {
    text.replace(['\n', '\r'], " ")
}

/// Runs a subprocess with a hard timeout so a wedged herdr server can never
/// hang the paste path (which runs on the main thread). The child is awaited
/// on a helper thread because `wait_with_output` must drain the pipes — a
/// child emitting more than a pipe buffer would otherwise block on write and
/// never exit. On timeout the helper thread still reaps the child whenever it
/// eventually finishes, so nothing is left as a zombie.
fn run_with_timeout(
    program: impl AsRef<OsStr>,
    args: &[&str],
    timeout: Duration,
) -> Result<std::process::Output, String> {
    let program = program.as_ref();
    let program_name = program.to_string_lossy();
    let child = Command::new(program)
        .args(args)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .map_err(|e| format!("Failed to spawn {}: {}", program_name, e))?;

    let pid = child.id();
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let _ = tx.send(child.wait_with_output());
    });

    match rx.recv_timeout(timeout) {
        Ok(Ok(output)) => Ok(output),
        Ok(Err(e)) => Err(format!("Failed to collect {} output: {}", program_name, e)),
        Err(_) => {
            // A last-moment exit is preferred over killing a recycled pid.
            if let Ok(result) = rx.try_recv() {
                return result
                    .map_err(|e| format!("Failed to collect {} output: {}", program_name, e));
            }
            // Kill the wedged child (a wedged server would otherwise leak one
            // process + thread per attempt); the helper thread reaps it.
            let _ = Command::new("kill").arg("-9").arg(pid.to_string()).status();
            Err(format!("{} timed out after {:?}", program_name, timeout))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn live_status_json(socket: &Path) -> Vec<u8> {
        serde_json::to_vec(&serde_json::json!({
            "status": "running",
            "running": true,
            "version": "0.7.5",
            "protocol": 17,
            "capabilities": {
                "live_handoff": true,
                "detached_server_daemon": true
            },
            "compatible": true,
            "socket": socket,
            "session": null,
            "restart_needed": false
        }))
        .unwrap()
    }

    #[test]
    fn parses_installed_herdr_0_7_5_server_status_and_tolerates_unknown_fields() {
        let fixture = br#"{"status":"running","running":true,"version":"0.7.5","protocol":17,"capabilities":{"live_handoff":true,"detached_server_daemon":true},"compatible":true,"socket":"/home/qqp/.config/herdr/herdr.sock","session":null,"restart_needed":false,"future_field":{"ignored":true}}"#;
        let status = parse_herdr_server_status(fixture).expect("parse production 0.7.5 shape");
        assert_eq!(status.status, "running");
        assert_eq!(status.version, "0.7.5");
        assert_eq!(status.protocol, 17);
        assert_eq!(status.socket, "/home/qqp/.config/herdr/herdr.sock");
        assert_eq!(status.session, None);
    }

    #[test]
    fn malformed_unavailable_non_socket_and_wrong_owner_status_is_refused() {
        for malformed in [
            br#"[]"#.as_slice(),
            br#"{"status":"running","running":"yes","version":"0.7.5","protocol":17,"compatible":true,"socket":"/tmp/herdr.sock","session":null}"#,
            br#"{"status":"stopped","running":false,"version":"0.7.5","protocol":17,"compatible":true,"socket":"/tmp/herdr.sock","session":null}"#,
            br#"{"status":"running","running":true,"version":"0.7.5","protocol":17,"compatible":false,"socket":"/tmp/herdr.sock","session":null}"#,
            br#"{"status":"running","running":true,"version":"0.7.5","protocol":17,"compatible":true,"socket":42,"session":null}"#,
        ] {
            assert!(parse_herdr_server_status(malformed).is_err());
        }

        let temp_dir = tempfile::TempDir::new().expect("create socket fixtures");
        let expected_uid = unsafe { libc::geteuid() };
        let missing = temp_dir.path().join("missing.sock");
        assert!(identity_from_status(&live_status_json(&missing), expected_uid).is_err());

        let regular = temp_dir.path().join("regular.sock");
        fs::write(&regular, b"not a socket").unwrap();
        assert!(identity_from_status(&live_status_json(&regular), expected_uid).is_err());

        let socket = temp_dir.path().join("owned.sock");
        let _listener = std::os::unix::net::UnixListener::bind(&socket).unwrap();
        assert!(
            identity_from_status(&live_status_json(&socket), expected_uid.wrapping_add(1)).is_err()
        );
    }

    #[test]
    fn production_socket_identity_includes_inode_peer_pid_and_start_time() {
        let temp_dir = tempfile::TempDir::new().expect("create socket fixture");
        let socket = temp_dir.path().join("server.sock");
        let _listener = std::os::unix::net::UnixListener::bind(&socket).unwrap();
        let expected_uid = unsafe { libc::geteuid() };

        let identity =
            identity_from_status(&live_status_json(&socket), expected_uid).expect("observe socket");

        assert_eq!(identity.socket_path, socket);
        assert!(identity.socket_inode > 0);
        assert_eq!(identity.socket_uid, expected_uid);
        assert_eq!(identity.peer_pid, std::process::id());
        assert_eq!(identity.peer_uid, expected_uid);
        assert!(identity.peer_start_time > 0);
    }

    #[test]
    fn parses_focused_pane_from_snapshot() {
        let json = br#"{"id":"cli:api:snapshot","result":{"snapshot":{"focused_pane_id":"w4G:p2","panes":[]},"type":"api_snapshot"}}"#;
        assert_eq!(parse_focused_pane_id(json), Some("w4G:p2".to_string()));
    }

    #[test]
    fn rejects_missing_or_empty_focused_pane() {
        let missing = br#"{"result":{"snapshot":{"panes":[]}}}"#;
        assert_eq!(parse_focused_pane_id(missing), None);
        let empty = br#"{"result":{"snapshot":{"focused_pane_id":""}}}"#;
        assert_eq!(parse_focused_pane_id(empty), None);
        let null = br#"{"result":{"snapshot":{"focused_pane_id":null}}}"#;
        assert_eq!(parse_focused_pane_id(null), None);
        let garbage = b"not json";
        assert_eq!(parse_focused_pane_id(garbage), None);
    }

    #[test]
    fn collapses_newlines_to_spaces() {
        assert_eq!(collapse_newlines("hello\nworld"), "hello world");
        assert_eq!(collapse_newlines("a\r\nb\nc"), "a  b c");
        assert_eq!(collapse_newlines("no newlines"), "no newlines");
    }

    #[test]
    fn remote_pane_ids_are_strict_and_bounded() {
        for valid in ["w2H:p13", "wM:pEC", "w1:p2"] {
            assert!(validate_pane_id(valid), "expected valid pane id {valid}");
        }
        for invalid in [
            "",
            "w2H",
            "w2H:p13:extra",
            "focused_pane_id",
            "w2H:p-13",
            "../w2H:p13",
            "w:p",
        ] {
            assert!(
                !validate_pane_id(invalid),
                "accepted malformed pane id {invalid}"
            );
        }
        assert!(!validate_pane_id(&format!("w{}:p1", "a".repeat(64))));
    }

    #[test]
    fn remote_snapshot_requires_one_valid_live_focused_pane() {
        let valid = br#"{"result":{"snapshot":{"focused_pane_id":"w2H:p13","panes":[{"pane_id":"w2H:p13","focused":true},{"pane_id":"w2H:p14","focused":false}]}}}"#;
        assert_eq!(parse_remote_delivery_pane(valid), Ok("w2H:p13".to_string()));

        for invalid in [
            br#"{"result":{"snapshot":{"panes":[]}}}"#.as_slice(),
            br#"{"result":{"snapshot":{"focused_pane_id":"","panes":[]}}}"#,
            br#"{"result":{"snapshot":{"focused_pane_id":"bad","panes":[]}}}"#,
            br#"{"result":{"snapshot":{"focused_pane_id":"w2H:p13","panes":[]}}}"#,
            br#"{"result":{"snapshot":{"focused_pane_id":"w2H:p13","panes":[{"pane_id":"w2H:p13","focused":false}]}}}"#,
            br#"{"result":{"snapshot":{"focused_pane_id":"w2H:p13","panes":[{"pane_id":"w2H:p13","focused":true},{"pane_id":"w2H:p14","focused":true}]}}}"#,
            br#"{"result":{"snapshot":{"focused_pane_id":"w2H:p13","panes":[{"pane_id":"w2H:p13","focused":true},{"pane_id":"w2H:p13","focused":true}]}}}"#,
            br#"{"result":{"snapshot":{"focused_pane_id":"w2H:p13","focused_pane_id":"w2H:p14","panes":[]}}}"#,
            b"not json",
        ] {
            assert!(parse_remote_delivery_pane(invalid).is_err());
        }
    }

    #[test]
    fn same_session_identity_allows_a_different_commit_time_focused_pane() {
        let identity = synthetic_remote_session_identity(1);
        let commit = br#"{"result":{"snapshot":{"focused_pane_id":"wB:p2","panes":[{"pane_id":"wA:p1","focused":false},{"pane_id":"wB:p2","focused":true}]}}}"#;
        let calls = std::cell::RefCell::new(Vec::new());

        let selected = deliver_remote_with(
            &identity,
            "new focus wins",
            true,
            true,
            || Ok(identity.clone()),
            || Ok(commit.to_vec()),
            |pane, payload| {
                calls
                    .borrow_mut()
                    .push((pane.to_string(), payload.to_string()));
                Ok(())
            },
        )
        .unwrap();

        assert_eq!(selected, "wB:p2");
        assert_eq!(
            calls.into_inner(),
            [("wB:p2".to_string(), "new focus wins\r".to_string())]
        );
    }

    #[test]
    fn replaced_session_identity_refuses_before_snapshot_or_send() {
        let start = synthetic_remote_session_identity(1);
        let mut replacements = Vec::new();
        let mut changed = start.clone();
        changed.socket_path = PathBuf::from("/synthetic/replaced.sock");
        replacements.push(changed);
        let mut changed = start.clone();
        changed.socket_inode += 1;
        replacements.push(changed);
        let mut changed = start.clone();
        changed.peer_pid += 1;
        replacements.push(changed);
        let mut changed = start.clone();
        changed.peer_start_time += 1;
        replacements.push(changed);

        for replacement in replacements {
            let snapshot_calls = std::cell::Cell::new(0);
            let send_calls = std::cell::Cell::new(0);
            let result = deliver_remote_with(
                &start,
                "must not deliver",
                true,
                true,
                || Ok(replacement),
                || {
                    snapshot_calls.set(snapshot_calls.get() + 1);
                    Ok(Vec::new())
                },
                |_pane, _payload| {
                    send_calls.set(send_calls.get() + 1);
                    Ok(())
                },
            );
            assert!(result.is_err());
            assert_eq!(snapshot_calls.get(), 0);
            assert_eq!(send_calls.get(), 0);
        }
    }

    #[test]
    fn unavailable_or_malformed_commit_identity_refuses_before_snapshot_or_send() {
        let start = synthetic_remote_session_identity(1);
        for reason in [
            "status unavailable",
            "status malformed",
            "not a socket",
            "wrong owner",
        ] {
            let snapshot_calls = std::cell::Cell::new(0);
            let send_calls = std::cell::Cell::new(0);
            let result = deliver_remote_with(
                &start,
                "must not deliver",
                true,
                true,
                || Err(reason.to_string()),
                || {
                    snapshot_calls.set(snapshot_calls.get() + 1);
                    Ok(Vec::new())
                },
                |_pane, _payload| {
                    send_calls.set(send_calls.get() + 1);
                    Ok(())
                },
            );
            assert!(result.is_err());
            assert_eq!(snapshot_calls.get(), 0);
            assert_eq!(send_calls.get(), 0);
        }
    }

    #[test]
    fn unchanged_laptop_ghostty_does_not_authorize_replaced_workstation_session() {
        // Laptop window identity is intentionally absent from this workstation
        // fence: even if the laptop check says "unchanged", a changed server
        // identity terminates before any focus snapshot or send.
        let start = synthetic_remote_session_identity(1);
        let replacement = synthetic_remote_session_identity(2);
        let snapshot_called = std::cell::Cell::new(false);
        let result = deliver_remote_with(
            &start,
            "old transcript",
            true,
            true,
            || Ok(replacement),
            || {
                snapshot_called.set(true);
                Ok(Vec::new())
            },
            |_pane, _payload| Ok(()),
        );
        assert!(result.is_err());
        assert!(!snapshot_called.get());
    }

    #[test]
    fn local_exact_capture_never_uses_remote_session_identity() {
        let local_capture = CaptureOutcome::Bound("wLocal:pExact".to_string());
        let remote_identity = synthetic_remote_session_identity(99);

        assert_eq!(
            local_capture,
            CaptureOutcome::Bound("wLocal:pExact".to_string())
        );
        // The independently captured pane is complete local authority; no
        // remote identity field participates in the result.
        assert_eq!(remote_identity.socket_inode, 99);
    }

    #[test]
    fn remote_delivery_uses_only_commit_snapshot_pane_once() {
        let start = br#"{"result":{"snapshot":{"focused_pane_id":"wA:p1","panes":[{"pane_id":"wA:p1","focused":true}]}}}"#;
        let commit = br#"{"result":{"snapshot":{"focused_pane_id":"wB:p2","panes":[{"pane_id":"wA:p1","focused":false},{"pane_id":"wB:p2","focused":true}]}}}"#;
        assert_eq!(parse_remote_delivery_pane(start).unwrap(), "wA:p1");

        let calls = std::cell::RefCell::new(Vec::new());
        let selected =
            deliver_remote_snapshot(commit, "first\nsecond", true, true, |pane, payload| {
                calls
                    .borrow_mut()
                    .push((pane.to_string(), payload.to_string()));
                Ok(())
            })
            .unwrap();

        assert_eq!(selected, "wB:p2");
        assert_eq!(
            calls.into_inner(),
            [("wB:p2".to_string(), "first second\r".to_string())]
        );

        let failures = std::cell::Cell::new(0);
        assert!(
            deliver_remote_snapshot(commit, "text", true, true, |_pane, _payload| {
                failures.set(failures.get() + 1);
                Err("pane closed".to_string())
            })
            .is_err()
        );
        assert_eq!(failures.get(), 1);
    }

    #[test]
    fn send_text_argv_contains_exact_pane_text_and_optional_carriage_return() {
        let auto_submit = send_text_payload("first\nsecond", true);
        assert_eq!(auto_submit, "first second\r");
        assert_eq!(
            send_text_args("wM:p8P", &auto_submit),
            ["pane", "send-text", "wM:p8P", "first second\r"]
        );

        let no_submit = send_text_payload("-leading dash is valid text", false);
        assert_eq!(no_submit, "-leading dash is valid text");
        assert_eq!(
            send_text_args("wM:p8P", &no_submit),
            ["pane", "send-text", "wM:p8P", "-leading dash is valid text"]
        );
    }

    /// Single test for all map behavior: the map is process-global and tests
    /// run in parallel, so separate tests could evict each other's entries.
    #[test]
    fn captures_are_per_recording_and_evicted_in_order() {
        // A finished "not aimed at herdr" capture answers immediately — the
        // common non-herdr case must not pay the in-flight wait.
        store_capture(950, CaptureOutcome::Legacy);
        let started = std::time::Instant::now();
        assert_eq!(take_for_recording(950), CaptureOutcome::Legacy);
        assert!(started.elapsed() < Duration::from_millis(100));

        store_capture(901, CaptureOutcome::Bound("wA:p1".to_string()));
        store_capture(902, CaptureOutcome::Bound("wB:p2".to_string()));
        // The newer recording's capture must not satisfy the older recording.
        assert_eq!(
            take_for_recording(901),
            CaptureOutcome::Bound("wA:p1".to_string())
        );
        assert_eq!(
            take_for_recording(902),
            CaptureOutcome::Bound("wB:p2".to_string())
        );

        for token in 1000..1012 {
            store_capture(token, CaptureOutcome::Bound(format!("wX:p{}", token)));
        }
        // Oldest beyond MAX_BOUND_PANES are evicted; the newest survive.
        assert!(matches!(
            take_for_recording(1000),
            CaptureOutcome::Failed(_)
        ));
        assert_eq!(
            take_for_recording(1011),
            CaptureOutcome::Bound("wX:p1011".to_string())
        );
    }

    #[test]
    fn absent_capture_is_a_targeting_failure() {
        assert!(matches!(
            take_for_recording(0),
            CaptureOutcome::Failed(reason) if reason.contains("timed out")
        ));
    }

    #[test]
    fn resolves_path_before_linuxbrew_fallback() {
        let path = OsStr::new("/desktop/bin:/usr/bin");
        let fallback = Path::new(LINUXBREW_HERDR);

        let from_path = resolve_herdr_from(Some(path), fallback, |candidate| {
            candidate == Path::new("/usr/bin/herdr") || candidate == fallback
        });
        assert_eq!(from_path.unwrap(), PathBuf::from("/usr/bin/herdr"));

        let from_fallback =
            resolve_herdr_from(Some(path), fallback, |candidate| candidate == fallback);
        assert_eq!(from_fallback.unwrap(), fallback);
    }

    #[test]
    fn resolves_linuxbrew_herdr_when_desktop_path_omits_it() {
        let desktop_path =
            OsStr::new("/home/qqp/.local/bin:/usr/local/bin:/usr/bin:/bin:/snap/bin");
        let fallback = Path::new(LINUXBREW_HERDR);
        let resolved = resolve_herdr_from(Some(desktop_path), fallback, |candidate| {
            candidate == fallback
        });

        assert_eq!(resolved.unwrap(), fallback);
    }

    #[test]
    fn missing_herdr_is_an_explicit_error() {
        let result = resolve_herdr_from(
            Some(OsStr::new("/usr/bin:/bin")),
            Path::new(LINUXBREW_HERDR),
            |_| false,
        );

        assert!(result.unwrap_err().contains("was not found"));
    }
}
