use crate::target_binding::{self, CaptureOutcome};
use log::warn;
use serde::Deserialize;
use std::ffi::OsStr;
use std::io;
use std::path::{Path, PathBuf};

const MARK_DIRECTORY: &str = "qq/dictation-private";

#[derive(Debug, Deserialize)]
struct PrivacyMark {
    version: u64,
    #[serde(rename = "paneId")]
    pane_id: String,
    #[serde(rename = "sessionId")]
    _session_id: String,
    #[serde(rename = "createdAt")]
    _created_at: String,
}

#[derive(Debug, PartialEq, Eq)]
pub(crate) struct PostProcessDecision {
    pub(crate) effective_post_process: bool,
    pub(crate) privacy_skip_pane: Option<String>,
}

impl PostProcessDecision {
    fn unchanged(requested: bool) -> Self {
        Self {
            effective_post_process: requested,
            privacy_skip_pane: None,
        }
    }

    fn skipped(pane_id: impl Into<String>) -> Self {
        Self {
            effective_post_process: false,
            privacy_skip_pane: Some(pane_id.into()),
        }
    }
}

pub(crate) fn local_post_process_decision(
    requested: bool,
    capture: &CaptureOutcome,
) -> PostProcessDecision {
    local_post_process_decision_with(requested, capture, is_marked)
}

fn local_post_process_decision_with(
    requested: bool,
    capture: &CaptureOutcome,
    marked: impl FnOnce(&str) -> bool,
) -> PostProcessDecision {
    if !requested {
        return PostProcessDecision::unchanged(false);
    }

    match capture {
        CaptureOutcome::Bound(pane_id) if marked(pane_id) => {
            PostProcessDecision::skipped(pane_id.clone())
        }
        CaptureOutcome::Bound(_) | CaptureOutcome::Legacy | CaptureOutcome::Failed(_) => {
            PostProcessDecision::unchanged(true)
        }
    }
}

pub(crate) fn remote_post_process_decision(requested: bool) -> PostProcessDecision {
    remote_post_process_decision_with(
        requested,
        target_binding::resolve_remote_focused_pane,
        is_marked,
        |message| warn!("{message}"),
    )
}

fn remote_post_process_decision_with(
    requested: bool,
    resolve_pane: impl FnOnce() -> Result<String, String>,
    marked: impl FnOnce(&str) -> bool,
    mut warn: impl FnMut(&str),
) -> PostProcessDecision {
    if !requested {
        return PostProcessDecision::unchanged(false);
    }

    match resolve_pane() {
        Ok(pane_id) if marked(&pane_id) => PostProcessDecision::skipped(pane_id),
        Ok(_) => PostProcessDecision::unchanged(true),
        Err(reason) => {
            warn(&format!(
                "Could not resolve the remote privacy target; failing toward privacy: {reason}"
            ));
            PostProcessDecision::skipped("<unresolved remote focus>")
        }
    }
}

pub(crate) fn is_marked(pane_id: &str) -> bool {
    is_marked_with(
        pane_id,
        || {
            resolve_state_home_from(
                std::env::var_os("XDG_STATE_HOME").as_deref(),
                std::env::var_os("HOME").as_deref(),
            )
        },
        |path| std::fs::read(path),
        |message| warn!("{message}"),
    )
}

fn is_marked_with(
    pane_id: &str,
    state_home: impl FnOnce() -> Result<PathBuf, String>,
    read: impl FnOnce(&Path) -> io::Result<Vec<u8>>,
    mut warn: impl FnMut(&str),
) -> bool {
    let path = match mark_path_with(pane_id, state_home) {
        Ok(path) => path,
        Err(error) => {
            warn(&format!(
                "Could not resolve a privacy mark for pane {pane_id}; failing toward privacy: {error}"
            ));
            return true;
        }
    };

    is_marked_at_with(&path, pane_id, read, warn)
}

fn mark_path_with(
    pane_id: &str,
    state_home: impl FnOnce() -> Result<PathBuf, String>,
) -> Result<PathBuf, String> {
    // Validate before resolving or joining any path. An invalid pane identity
    // must never become even part of a candidate filesystem path.
    if !target_binding::validate_pane_id(pane_id) {
        return Err("pane id is invalid".to_string());
    }

    let state_home = state_home()?;
    if !state_home.is_absolute() {
        return Err("state home is not an absolute path".to_string());
    }
    Ok(state_home
        .join(MARK_DIRECTORY)
        .join(format!("{pane_id}.json")))
}

fn resolve_state_home_from(
    xdg_state_home: Option<&OsStr>,
    home: Option<&OsStr>,
) -> Result<PathBuf, String> {
    if let Some(xdg_state_home) = xdg_state_home.filter(|path| !path.is_empty()) {
        let path = PathBuf::from(xdg_state_home);
        if path.is_absolute() {
            return Ok(path);
        }
        return Err("XDG_STATE_HOME is not an absolute path".to_string());
    }

    let home = home
        .filter(|path| !path.is_empty())
        .ok_or_else(|| "neither XDG_STATE_HOME nor HOME is available".to_string())?;
    let home = PathBuf::from(home);
    if !home.is_absolute() {
        return Err("HOME is not an absolute path".to_string());
    }
    Ok(home.join(".local/state"))
}

fn is_marked_at_with(
    path: &Path,
    pane_id: &str,
    read: impl FnOnce(&Path) -> io::Result<Vec<u8>>,
    mut warn: impl FnMut(&str),
) -> bool {
    let contents = match read(path) {
        Ok(contents) => contents,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return false,
        Err(error) => {
            warn(&format!(
                "Privacy mark for pane {pane_id} is unreadable; failing toward privacy: {error}"
            ));
            return true;
        }
    };

    match serde_json::from_slice::<PrivacyMark>(&contents) {
        Ok(mark) if mark.version == 1 => mark.pane_id == pane_id,
        Ok(mark) => {
            warn(&format!(
                "Privacy mark for pane {pane_id} has unsupported version {}; failing toward privacy",
                mark.version
            ));
            true
        }
        Err(error) => {
            warn(&format!(
                "Privacy mark for pane {pane_id} is malformed; failing toward privacy: {error}"
            ));
            true
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::{Cell, RefCell};

    const PANE: &str = "w4G:p2";

    fn valid_mark(pane_id: &str) -> Vec<u8> {
        serde_json::to_vec(&serde_json::json!({
            "version": 1,
            "paneId": pane_id,
            "sessionId": "session-1",
            "createdAt": "2026-08-10T19:00:00Z"
        }))
        .unwrap()
    }

    #[test]
    fn marked_bound_pane_makes_effective_post_processing_false() {
        let decision = local_post_process_decision_with(
            true,
            &CaptureOutcome::Bound(PANE.to_string()),
            |_| true,
        );

        assert_eq!(
            decision,
            PostProcessDecision {
                effective_post_process: false,
                privacy_skip_pane: Some(PANE.to_string()),
            }
        );
    }

    #[test]
    fn unmarked_bound_pane_retains_requested_post_processing() {
        let decision = local_post_process_decision_with(
            true,
            &CaptureOutcome::Bound(PANE.to_string()),
            |_| false,
        );

        assert_eq!(decision, PostProcessDecision::unchanged(true));
    }

    #[test]
    fn legacy_and_failed_captures_retain_requested_post_processing() {
        assert_eq!(
            local_post_process_decision_with(true, &CaptureOutcome::Legacy, |_| {
                panic!("legacy capture must not read a mark")
            }),
            PostProcessDecision::unchanged(true)
        );
        assert_eq!(
            local_post_process_decision_with(
                true,
                &CaptureOutcome::Failed("synthetic capture failure".to_string()),
                |_| panic!("failed capture must not read a mark"),
            ),
            PostProcessDecision::unchanged(true)
        );
    }

    #[test]
    fn readable_matching_and_mismatched_marks_follow_the_contract() {
        let warnings = RefCell::new(Vec::new());
        assert!(is_marked_at_with(
            Path::new("unused"),
            PANE,
            |_| Ok(valid_mark(PANE)),
            |message| warnings.borrow_mut().push(message.to_string()),
        ));
        assert!(!is_marked_at_with(
            Path::new("unused"),
            PANE,
            |_| Ok(valid_mark("wElse:p9")),
            |message| warnings.borrow_mut().push(message.to_string()),
        ));
        assert!(warnings.borrow().is_empty());
    }

    #[test]
    fn missing_mark_is_unmarked() {
        let warnings = RefCell::new(Vec::new());
        assert!(!is_marked_at_with(
            Path::new("unused"),
            PANE,
            |_| Err(io::Error::from(io::ErrorKind::NotFound)),
            |message| warnings.borrow_mut().push(message.to_string()),
        ));
        assert!(warnings.borrow().is_empty());
    }

    #[test]
    fn malformed_and_unsupported_marks_fail_toward_marked_and_warn() {
        for contents in [
            b"not-json".to_vec(),
            serde_json::to_vec(&serde_json::json!({
                "version": 2,
                "paneId": PANE,
                "sessionId": "session-1",
                "createdAt": "2026-08-10T19:00:00Z"
            }))
            .unwrap(),
        ] {
            let warnings = RefCell::new(Vec::new());
            assert!(is_marked_at_with(
                Path::new("unused"),
                PANE,
                |_| Ok(contents),
                |message| warnings.borrow_mut().push(message.to_string()),
            ));
            assert_eq!(warnings.borrow().len(), 1);
        }
    }

    #[test]
    fn unreadable_mark_fails_toward_marked_and_warns() {
        let warnings = RefCell::new(Vec::new());
        assert!(is_marked_at_with(
            Path::new("unused"),
            PANE,
            |_| Err(io::Error::from(io::ErrorKind::PermissionDenied)),
            |message| warnings.borrow_mut().push(message.to_string()),
        ));
        assert_eq!(warnings.borrow().len(), 1);
    }

    #[test]
    fn invalid_pane_is_refused_before_state_home_or_path_construction() {
        let resolver_called = Cell::new(false);
        let result = mark_path_with("../../provider-secret", || {
            resolver_called.set(true);
            Ok(PathBuf::from("/unused"))
        });

        assert!(result.is_err());
        assert!(!resolver_called.get());
    }

    #[test]
    fn unavailable_safe_state_home_fails_toward_marked_and_warns() {
        let read_called = Cell::new(false);
        let warnings = RefCell::new(Vec::new());
        assert!(is_marked_with(
            PANE,
            || Err("synthetic state-home failure".to_string()),
            |_| {
                read_called.set(true);
                Ok(Vec::new())
            },
            |message| warnings.borrow_mut().push(message.to_string()),
        ));
        assert!(!read_called.get());
        assert_eq!(warnings.borrow().len(), 1);
    }

    #[test]
    fn state_home_uses_xdg_then_absolute_home_fallback() {
        assert_eq!(
            resolve_state_home_from(Some(OsStr::new("/state")), Some(OsStr::new("/home/me")))
                .unwrap(),
            PathBuf::from("/state")
        );
        assert_eq!(
            resolve_state_home_from(None, Some(OsStr::new("/home/me"))).unwrap(),
            PathBuf::from("/home/me/.local/state")
        );
        assert!(resolve_state_home_from(Some(OsStr::new("relative")), None).is_err());
        assert!(resolve_state_home_from(None, Some(OsStr::new("relative"))).is_err());
    }

    #[test]
    fn remote_resolution_failure_fails_toward_marked() {
        let warnings = RefCell::new(Vec::new());
        let decision = remote_post_process_decision_with(
            true,
            || Err("synthetic snapshot failure".to_string()),
            |_| panic!("failed resolution must not read a mark"),
            |message| warnings.borrow_mut().push(message.to_string()),
        );

        assert_eq!(decision.effective_post_process, false);
        assert_eq!(
            decision.privacy_skip_pane.as_deref(),
            Some("<unresolved remote focus>")
        );
        assert_eq!(warnings.borrow().len(), 1);
    }

    #[test]
    fn disabled_post_processing_does_not_consult_targets_or_marks() {
        assert_eq!(
            remote_post_process_decision_with(
                false,
                || panic!("disabled post-processing must not resolve remote focus"),
                |_| panic!("disabled post-processing must not read a mark"),
                |_| panic!("disabled post-processing must not warn"),
            ),
            PostProcessDecision::unchanged(false)
        );
    }
}
