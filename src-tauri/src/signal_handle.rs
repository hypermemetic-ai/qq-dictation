use crate::TranscriptionCoordinator;
#[cfg(unix)]
use log::debug;
use log::warn;
use tauri::{AppHandle, Manager};

#[cfg(unix)]
use signal_hook::consts::{SIGUSR1, SIGUSR2};
#[cfg(unix)]
use signal_hook::iterator::Signals;
#[cfg(unix)]
use std::thread;

#[cfg(target_os = "linux")]
pub fn ptt_start_signal() -> i32 {
    libc::SIGRTMIN()
}

#[cfg(target_os = "linux")]
pub fn ptt_stop_signal() -> i32 {
    libc::SIGRTMIN() + 1
}

#[cfg(unix)]
pub fn transcription_signals() -> Vec<i32> {
    #[cfg(target_os = "linux")]
    {
        vec![SIGUSR1, SIGUSR2, ptt_start_signal(), ptt_stop_signal()]
    }
    #[cfg(not(target_os = "linux"))]
    {
        vec![SIGUSR1, SIGUSR2]
    }
}

/// Send a transcription input to the coordinator.
/// Used by signal handlers, CLI flags, and any other external trigger.
pub fn send_transcription_input(app: &AppHandle, binding_id: &str, source: &str) {
    if let Some(c) = app.try_state::<TranscriptionCoordinator>() {
        c.send_input(binding_id, source, true, false);
    } else {
        warn!("TranscriptionCoordinator not initialized");
    }
}

#[cfg(target_os = "linux")]
fn send_ptt_input(app: &AppHandle, source: &str, is_pressed: bool) {
    if let Some(c) = app.try_state::<TranscriptionCoordinator>() {
        c.send_input("transcribe", source, is_pressed, true);
    } else {
        warn!("TranscriptionCoordinator not initialized");
    }
}

#[cfg(unix)]
pub fn setup_signal_handler(app_handle: AppHandle, mut signals: Signals) {
    #[cfg(target_os = "linux")]
    let ptt_start = ptt_start_signal();
    #[cfg(target_os = "linux")]
    let ptt_stop = ptt_stop_signal();
    #[cfg(target_os = "linux")]
    debug!(
        "Signal handlers registered (SIGUSR1, SIGUSR2, SIGRTMIN={ptt_start}, SIGRTMIN+1={ptt_stop})"
    );
    #[cfg(not(target_os = "linux"))]
    debug!("Signal handlers registered (SIGUSR1, SIGUSR2)");
    thread::spawn(move || {
        for sig in signals.forever() {
            match sig {
                SIGUSR1 => {
                    debug!("Received SIGUSR1");
                    send_transcription_input(
                        &app_handle,
                        "transcribe_with_post_process",
                        "SIGUSR1",
                    );
                }
                SIGUSR2 => {
                    debug!("Received SIGUSR2");
                    send_transcription_input(&app_handle, "transcribe", "SIGUSR2");
                }
                #[cfg(target_os = "linux")]
                sig if sig == ptt_start => {
                    debug!("Received SIGRTMIN (PTT press)");
                    send_ptt_input(&app_handle, "SIGRTMIN", true);
                }
                #[cfg(target_os = "linux")]
                sig if sig == ptt_stop => {
                    debug!("Received SIGRTMIN+1 (PTT release)");
                    send_ptt_input(&app_handle, "SIGRTMIN+1", false);
                }
                _ => continue,
            }
        }
    });
}

#[cfg(all(test, target_os = "linux"))]
mod tests {
    use super::*;

    #[test]
    fn ptt_signals_are_distinct_realtime_signals() {
        assert_eq!(ptt_start_signal(), libc::SIGRTMIN());
        assert_eq!(ptt_stop_signal(), libc::SIGRTMIN() + 1);
        assert_ne!(ptt_start_signal(), ptt_stop_signal());
        assert!(ptt_stop_signal() <= libc::SIGRTMAX());
    }
}
