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
//! own entry; anything else falls back to the ordinary paste path.
//!
//! Linux/X11-only in practice. Every public function degrades to a no-op or
//! `None` off the supported path so the caller's fallback (the existing
//! focus-based paste) stays intact.

#[cfg(target_os = "linux")]
use log::{debug, warn};
use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::Duration;

#[cfg(target_os = "linux")]
use std::process::Command;
use tauri::AppHandle;

/// Title herdr's client sets on its terminal window; used to tell "operator
/// is dictating into a herdr pane" apart from "herdr merely runs somewhere".
#[cfg(target_os = "linux")]
const HERDR_WINDOW_TITLE: &str = "herdr";

/// In-flight capture results, keyed by recording token. The capture thread
/// always writes an entry — `Some(pane)` when the recording was aimed at a
/// herdr pane, `None` when it wasn't — so a missing entry means "capture
/// still running", not "nothing bound". Bounded: old entries are evicted
/// once the map outgrows a handful of recordings (a binding whose
/// transcription never pastes — e.g. cancelled — would otherwise linger).
static CAPTURES: Mutex<Option<HashMap<u64, Option<String>>>> = Mutex::new(None);
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

    #[cfg(target_os = "linux")]
    std::thread::spawn(move || {
        let pane = if crate::settings::get_settings(&app).herdr_binding_enabled {
            focused_herdr_pane()
        } else {
            None
        };
        match &pane {
            Some(pane_id) => debug!("Bound dictation #{} to herdr pane {}", token, pane_id),
            None => debug!("Dictation #{} not aimed at a herdr pane", token),
        }
        store_capture(token, pane);
    });

    token
}

/// The token of the most recently started recording; read at `stop` time.
pub fn latest_token() -> u64 {
    LATEST_TOKEN.load(Ordering::SeqCst)
}

#[cfg(target_os = "linux")]
fn store_capture(token: u64, capture: Option<String>) {
    // into_inner: a poisoned map (panic while locked) must degrade to "no
    // binding", not to silently disabling capture forever.
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

/// Takes the pane bound to *this* recording, if any. A finished capture —
/// including "recording wasn't aimed at a herdr pane" — answers immediately;
/// only a capture still in flight waits (bounded), because a short streaming
/// dictation can outrun the capture thread. After the wait, "no entry" means
/// fall back to the focus-based paste.
pub fn take_for_recording(token: u64) -> Option<String> {
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
            return None;
        }
        std::thread::sleep(Duration::from_millis(10));
    }
}

/// The focused herdr pane, but only when the herdr window itself is the
/// active X11 window — otherwise the snapshot's focused pane is just stale
/// state from before the operator moved to another app.
#[cfg(target_os = "linux")]
fn focused_herdr_pane() -> Option<String> {
    if crate::utils::is_wayland() {
        return None;
    }
    let title = active_window_title()?;
    if title != HERDR_WINDOW_TITLE {
        return None;
    }
    let output = run_with_timeout("herdr", &["api", "snapshot"], Duration::from_secs(2)).ok()?;
    if !output.status.success() {
        warn!(
            "herdr api snapshot failed: {}",
            String::from_utf8_lossy(&output.stderr)
        );
        return None;
    }
    parse_focused_pane_id(&output.stdout)
}

#[cfg(target_os = "linux")]
fn active_window_title() -> Option<String> {
    let output = run_with_timeout(
        "xdotool",
        &["getactivewindow", "getwindowname"],
        Duration::from_millis(500),
    )
    .ok()?;
    if !output.status.success() {
        return None;
    }
    Some(String::from_utf8_lossy(&output.stdout).trim().to_string())
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

/// Types `text` into the pane's PTY. Newlines are collapsed to spaces: a raw
/// PTY write is not bracketed paste, so a literal newline would act as Enter
/// and submit a half-delivered message.
#[cfg(target_os = "linux")]
pub fn deliver(pane_id: &str, text: &str) -> Result<(), String> {
    let text = collapse_newlines(text);
    // `--` guards transcriptions that start with a dash from clap's flag parser.
    let output = run_with_timeout(
        "herdr",
        &["pane", "send-text", pane_id, "--", &text],
        Duration::from_secs(2),
    )?;
    if !output.status.success() {
        return Err(format!(
            "herdr pane send-text failed: {}",
            String::from_utf8_lossy(&output.stderr)
        ));
    }
    Ok(())
}

/// Sends Enter to the pane (auto-submit on the herdr path). Enter is the
/// only submit key that makes sense for a terminal pane; the
/// `auto_submit_key` setting's chorded variants target GUI apps.
#[cfg(target_os = "linux")]
pub fn send_enter(pane_id: &str) -> Result<(), String> {
    let output = run_with_timeout(
        "herdr",
        &["pane", "send-keys", pane_id, "enter"],
        Duration::from_secs(2),
    )?;
    if !output.status.success() {
        return Err(format!(
            "herdr pane send-keys failed: {}",
            String::from_utf8_lossy(&output.stderr)
        ));
    }
    Ok(())
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
#[cfg(target_os = "linux")]
fn run_with_timeout(
    program: &str,
    args: &[&str],
    timeout: Duration,
) -> Result<std::process::Output, String> {
    let child = Command::new(program)
        .args(args)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .map_err(|e| format!("Failed to spawn {}: {}", program, e))?;

    let pid = child.id();
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let _ = tx.send(child.wait_with_output());
    });

    match rx.recv_timeout(timeout) {
        Ok(Ok(output)) => Ok(output),
        Ok(Err(e)) => Err(format!("Failed to collect {} output: {}", program, e)),
        Err(_) => {
            // A last-moment exit is preferred over killing a recycled pid.
            if let Ok(result) = rx.try_recv() {
                return result.map_err(|e| format!("Failed to collect {} output: {}", program, e));
            }
            // Kill the wedged child (a wedged server would otherwise leak one
            // process + thread per attempt); the helper thread reaps it.
            let _ = Command::new("kill").arg("-9").arg(pid.to_string()).status();
            Err(format!("{} timed out after {:?}", program, timeout))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

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

    /// Single test for all map behavior: the map is process-global and tests
    /// run in parallel, so separate tests could evict each other's entries.
    #[cfg(target_os = "linux")]
    #[test]
    fn captures_are_per_recording_and_evicted_in_order() {
        // A finished "not aimed at herdr" capture answers immediately — the
        // common non-herdr case must not pay the in-flight wait.
        store_capture(950, None);
        let started = std::time::Instant::now();
        assert_eq!(take_for_recording(950), None);
        assert!(started.elapsed() < Duration::from_millis(100));

        store_capture(901, Some("wA:p1".to_string()));
        store_capture(902, Some("wB:p2".to_string()));
        // The newer recording's capture must not satisfy the older recording.
        assert_eq!(take_for_recording(901), Some("wA:p1".to_string()));
        // Taken entries are consumed.
        assert_eq!(take_for_recording(901), None);
        assert_eq!(take_for_recording(902), Some("wB:p2".to_string()));

        for token in 1000..1012 {
            store_capture(token, Some(format!("wX:p{}", token)));
        }
        // Oldest beyond MAX_BOUND_PANES are evicted; the newest survive.
        assert_eq!(take_for_recording(1000), None);
        assert_eq!(take_for_recording(1011), Some("wX:p1011".to_string()));
    }

    #[test]
    fn unknown_or_zero_token_has_no_binding() {
        assert_eq!(take_for_recording(0), None);
        assert_eq!(take_for_recording(999_999), None);
    }
}
