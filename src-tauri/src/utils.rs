use crate::managers::audio::AudioRecordingManager;
use crate::managers::transcription::TranscriptionManager;
use crate::operation::OperationOwner;
use crate::shortcut;
use crate::TranscriptionCoordinator;
use log::info;
use std::sync::Arc;
use tauri::{AppHandle, Manager};

// Re-export all utility modules for easy access
// pub use crate::audio_feedback::*;
pub use crate::clipboard::*;
pub use crate::overlay::*;
pub use crate::tray::*;

/// Request cancellation from a workstation-local control. The coordinator
/// applies it only when a local source owns the current operation; it cannot
/// cancel a remote request merely because that request happens to be active.
pub fn cancel_current_operation(app: &AppHandle) {
    if let Some(coordinator) = app.try_state::<TranscriptionCoordinator>() {
        coordinator.request_local_cancel();
    } else {
        log::warn!("Ignoring cancellation before TranscriptionCoordinator initialization");
    }
}

/// Execute cancellation after the coordinator has authenticated `owner`.
/// `recording_stage` distinguishes live capture from processing, where the
/// recorder has already returned to Idle but its cancellation generation still
/// gates post-processing and delivery.
pub(crate) fn cancel_owned_operation(
    app: &AppHandle,
    owner: &OperationOwner,
    recording_stage: bool,
) {
    info!("Cancelling operation owned by {owner}");

    if owner.is_local() {
        shortcut::unregister_cancel_shortcut(app);
    }

    let audio_manager = app.state::<Arc<AudioRecordingManager>>();
    if recording_stage {
        if !audio_manager.cancel_owned(owner) {
            log::warn!("Cancellation owner no longer matches audio capture: {owner}");
            return;
        }
    } else {
        audio_manager.cancel_processing();
    }

    let transcription = app.state::<Arc<TranscriptionManager>>();
    transcription.cancel_stream();
    change_tray_icon(app, crate::tray::TrayIconState::Idle);
    hide_recording_overlay(app);
    transcription.maybe_unload_immediately("cancellation");

    info!("Operation cancellation completed for {owner}");
}

/// Check if using the Wayland display server protocol
pub fn is_wayland() -> bool {
    std::env::var("WAYLAND_DISPLAY").is_ok()
        || std::env::var("XDG_SESSION_TYPE")
            .map(|v| v.to_lowercase() == "wayland")
            .unwrap_or(false)
}

/// Check if running on KDE Plasma desktop environment
pub fn is_kde_plasma() -> bool {
    std::env::var("XDG_CURRENT_DESKTOP")
        .map(|v| v.to_uppercase().contains("KDE"))
        .unwrap_or(false)
        || std::env::var("KDE_SESSION_VERSION").is_ok()
}

/// Check if running on KDE Plasma with Wayland
pub fn is_kde_wayland() -> bool {
    is_wayland() && is_kde_plasma()
}
