use crate::TranscriptionCoordinator;
use log::debug;
use log::warn;
use tauri::{AppHandle, Manager};

use signal_hook::consts::{SIGUSR1, SIGUSR2};
use signal_hook::iterator::Signals;
use std::thread;

pub fn ptt_start_signal() -> i32 {
    libc::SIGRTMIN()
}

pub fn ptt_stop_signal() -> i32 {
    libc::SIGRTMIN() + 1
}

// Visible Space dictation mode (qq-dictation). Realtime signal numbers
// preserve safety ordering when several are pending: prepare, commit, off,
// Space, then Delete.
pub fn mode_prepare_signal() -> i32 {
    libc::SIGRTMIN() + 2
}

pub fn mode_on_signal() -> i32 {
    libc::SIGRTMIN() + 3
}

pub fn mode_off_signal() -> i32 {
    libc::SIGRTMIN() + 4
}

pub fn mode_space_signal() -> i32 {
    libc::SIGRTMIN() + 5
}

pub fn mode_delete_signal() -> i32 {
    libc::SIGRTMIN() + 6
}

pub fn transcription_signals() -> Vec<i32> {
    vec![
        SIGUSR1,
        SIGUSR2,
        ptt_start_signal(),
        ptt_stop_signal(),
        mode_prepare_signal(),
        mode_on_signal(),
        mode_off_signal(),
        mode_space_signal(),
        mode_delete_signal(),
    ]
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

fn send_ptt_input(app: &AppHandle, source: &str, is_pressed: bool) {
    if let Some(c) = app.try_state::<TranscriptionCoordinator>() {
        c.send_input("transcribe", source, is_pressed, true);
    } else {
        warn!("TranscriptionCoordinator not initialized");
    }
}

/// Route a dictation-mode command to the coordinator. Mode signals are commands
/// (not raw key events), so the signal thread never calls pipeline actions
/// directly — the coordinator serialises them with every other input.
fn send_mode_input(app: &AppHandle, command: ModeCommand) {
    if let Some(c) = app.try_state::<TranscriptionCoordinator>() {
        match command {
            ModeCommand::Prepare => c.mode_prepare(),
            ModeCommand::On => c.mode_on(),
            ModeCommand::Off => c.mode_off(),
            ModeCommand::Space => c.mode_space("transcribe"),
            ModeCommand::Delete => c.mode_delete(),
        }
    } else {
        warn!("TranscriptionCoordinator not initialized");
    }
}

enum ModeCommand {
    Prepare,
    On,
    Off,
    Space,
    Delete,
}

pub fn setup_signal_handler(app_handle: AppHandle, mut signals: Signals) {
    let ptt_start = ptt_start_signal();
    let ptt_stop = ptt_stop_signal();
    let mode_prepare = mode_prepare_signal();
    let mode_on = mode_on_signal();
    let mode_off = mode_off_signal();
    let mode_space = mode_space_signal();
    let mode_delete = mode_delete_signal();
    debug!(
        "Signal handlers registered (SIGUSR1, SIGUSR2, SIGRTMIN={ptt_start}, SIGRTMIN+1={ptt_stop}, \
         mode prepare={mode_prepare}, mode on={mode_on}, mode off={mode_off}, mode space={mode_space}, mode delete={mode_delete})"
    );
    thread::spawn(move || {
        for sig in signals.forever() {
            match sig {
                SIGUSR1 => {
                    // Keep catching leftover SIGUSR1 so an unidentified sender
                    // cannot terminate Handy. It now starts the same raw path
                    // as SIGUSR2; there is no second pass.
                    debug!("Received SIGUSR1");
                    send_transcription_input(&app_handle, "transcribe", "SIGUSR1");
                }
                SIGUSR2 => {
                    debug!("Received SIGUSR2");
                    send_transcription_input(&app_handle, "transcribe", "SIGUSR2");
                }
                sig if sig == ptt_start => {
                    debug!("Received SIGRTMIN (PTT press)");
                    send_ptt_input(&app_handle, "SIGRTMIN", true);
                }
                sig if sig == ptt_stop => {
                    debug!("Received SIGRTMIN+1 (PTT release)");
                    send_ptt_input(&app_handle, "SIGRTMIN+1", false);
                }
                sig if sig == mode_prepare => {
                    debug!("Received SIGRTMIN+2 (dictation mode prepare)");
                    send_mode_input(&app_handle, ModeCommand::Prepare);
                }
                sig if sig == mode_on => {
                    debug!("Received SIGRTMIN+3 (dictation mode on)");
                    send_mode_input(&app_handle, ModeCommand::On);
                }
                sig if sig == mode_off => {
                    debug!("Received SIGRTMIN+4 (dictation mode off)");
                    send_mode_input(&app_handle, ModeCommand::Off);
                }
                sig if sig == mode_space => {
                    debug!("Received SIGRTMIN+5 (dictation mode Space)");
                    send_mode_input(&app_handle, ModeCommand::Space);
                }
                sig if sig == mode_delete => {
                    debug!("Received SIGRTMIN+6 (dictation mode Delete)");
                    send_mode_input(&app_handle, ModeCommand::Delete);
                }
                _ => continue,
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ptt_signals_are_distinct_realtime_signals() {
        assert_eq!(ptt_start_signal(), libc::SIGRTMIN());
        assert_eq!(ptt_stop_signal(), libc::SIGRTMIN() + 1);
        assert_ne!(ptt_start_signal(), ptt_stop_signal());
        assert!(ptt_stop_signal() <= libc::SIGRTMAX());
    }

    #[test]
    fn all_handled_realtime_signals_are_distinct_and_in_bounds() {
        let signals = [
            ptt_start_signal(),
            ptt_stop_signal(),
            mode_prepare_signal(),
            mode_on_signal(),
            mode_off_signal(),
            mode_space_signal(),
            mode_delete_signal(),
        ];
        for (index, signal) in signals.iter().enumerate() {
            assert!(
                *signal >= libc::SIGRTMIN() && *signal <= libc::SIGRTMAX(),
                "signal {signal} outside the realtime range"
            );
            for other in &signals[index + 1..] {
                assert_ne!(signal, other, "duplicate realtime signal {signal}");
            }
        }
        assert!(mode_prepare_signal() < mode_on_signal());
        assert!(mode_on_signal() < mode_off_signal());
        assert!(mode_off_signal() < mode_space_signal());
        assert!(mode_space_signal() < mode_delete_signal());
    }

    #[test]
    fn transcription_signals_cover_every_handled_input() {
        let handled = transcription_signals();
        for signal in [
            SIGUSR1,
            SIGUSR2,
            ptt_start_signal(),
            ptt_stop_signal(),
            mode_prepare_signal(),
            mode_on_signal(),
            mode_off_signal(),
            mode_space_signal(),
            mode_delete_signal(),
        ] {
            assert!(handled.contains(&signal), "missing signal {signal}");
        }
        assert_eq!(handled.len(), 9);
    }

}
