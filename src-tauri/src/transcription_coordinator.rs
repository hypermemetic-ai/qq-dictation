use crate::actions::ACTION_MAP;
use crate::managers::audio::AudioRecordingManager;
use log::{debug, error, warn};
use std::sync::mpsc::{self, Sender};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Manager};

const DEBOUNCE: Duration = Duration::from_millis(30);
const RELEASE_GRACE: Duration = Duration::from_millis(50);

/// Descriptive hotkey string for dictation-mode recordings; appears only in
/// logs, never in shortcut registration.
const MODE_HOTKEY: &str = "Dictation mode (Space)";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum PttAction {
    Passthrough,
    DeferRelease,
    CancelRelease,
}

struct PendingRelease {
    binding_id: String,
    hotkey_string: String,
    deadline: Instant,
}

/// Commands processed sequentially by the coordinator thread.
enum Command {
    Input {
        binding_id: String,
        hotkey_string: String,
        is_pressed: bool,
        push_to_talk: bool,
    },
    Cancel {
        recording_was_active: bool,
    },
    ProcessingFinished,
    // Visible Space dictation mode (qq-dictation). Right-Control arms/exits;
    // while armed, Space toggles recording and Delete cancels active work.
    ModePrepare,
    ModeOn,
    ModeOff,
    ModeSpace {
        binding_id: String,
    },
    ModeDelete,
}

/// Pipeline lifecycle, owned exclusively by the coordinator thread.
enum Stage {
    Idle,
    Recording(String), // binding_id
    Processing,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum PttLifecycleAction {
    Start,
    Stop,
    Ignore,
}

fn classify_ptt_lifecycle(stage: &Stage, binding_id: &str, is_pressed: bool) -> PttLifecycleAction {
    match (stage, is_pressed) {
        (Stage::Idle, true) => PttLifecycleAction::Start,
        (Stage::Recording(id), false) if id == binding_id => PttLifecycleAction::Stop,
        _ => PttLifecycleAction::Ignore,
    }
}

fn classify_ptt_event(
    pending_release_binding: Option<&str>,
    is_pressed: bool,
    push_to_talk: bool,
    binding_id: &str,
    recording_binding: Option<&str>,
) -> PttAction {
    if !push_to_talk {
        return PttAction::Passthrough;
    }

    if is_pressed {
        if pending_release_binding == Some(binding_id) {
            PttAction::CancelRelease
        } else {
            PttAction::Passthrough
        }
    } else if recording_binding == Some(binding_id) && pending_release_binding.is_none() {
        PttAction::DeferRelease
    } else {
        PttAction::Passthrough
    }
}

/// What a dictation-mode Space press does, given the armed flag and the
/// pipeline stage owned by the coordinator.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ModeSpaceAction {
    Start,
    Stop,
    Ignore,
}

fn classify_mode_space(armed: bool, stage: &Stage) -> ModeSpaceAction {
    if !armed {
        return ModeSpaceAction::Ignore;
    }
    match stage {
        Stage::Idle => ModeSpaceAction::Start,
        Stage::Recording(_) => ModeSpaceAction::Stop,
        // Presses while a transcript is still in flight are ignored; they must
        // never invert into a start once processing ends.
        Stage::Processing => ModeSpaceAction::Ignore,
    }
}

/// Delete cancels only when the mode is armed and there is active work.
fn mode_delete_is_active(armed: bool, stage: &Stage) -> bool {
    armed && matches!(stage, Stage::Recording(_) | Stage::Processing)
}

/// Serialises all transcription lifecycle events through a single thread
/// to eliminate race conditions between keyboard shortcuts, signals, and
/// the async transcribe-paste pipeline.
pub struct TranscriptionCoordinator {
    tx: Sender<Command>,
}

pub fn is_transcribe_binding(id: &str) -> bool {
    id == "transcribe" || id == "transcribe_with_post_process"
}

impl TranscriptionCoordinator {
    pub fn new(app: AppHandle) -> Self {
        let (tx, rx) = mpsc::channel();

        thread::spawn(move || {
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                let mut stage = Stage::Idle;
                let mut last_press: Option<Instant> = None;
                let mut pending_release: Option<PendingRelease> = None;
                // Visible Space dictation mode (qq-dictation). Defaults to off
                // on every start so a bridge or Handy restart never leaves the
                // mode armed.
                let mut prepared = false;
                let mut armed = false;

                loop {
                    let cmd = if let Some(pending) = &pending_release {
                        match rx.recv_timeout(
                            pending.deadline.saturating_duration_since(Instant::now()),
                        ) {
                            Ok(cmd) => cmd,
                            Err(mpsc::RecvTimeoutError::Timeout) => {
                                if let Some(pending) = pending_release.take() {
                                    if matches!(&stage, Stage::Recording(id) if id == &pending.binding_id)
                                    {
                                        stop(
                                            &app,
                                            &mut stage,
                                            &pending.binding_id,
                                            &pending.hotkey_string,
                                        );
                                    }
                                }
                                continue;
                            }
                            Err(mpsc::RecvTimeoutError::Disconnected) => break,
                        }
                    } else {
                        match rx.recv() {
                            Ok(cmd) => cmd,
                            Err(_) => break,
                        }
                    };

                    match cmd {
                        Command::Input {
                            binding_id,
                            hotkey_string,
                            is_pressed,
                            push_to_talk,
                        } => {
                            let pending_release_binding = pending_release
                                .as_ref()
                                .map(|pending| pending.binding_id.as_str());
                            let recording_binding = match &stage {
                                Stage::Recording(id) => Some(id.as_str()),
                                _ => None,
                            };

                            match classify_ptt_event(
                                pending_release_binding,
                                is_pressed,
                                push_to_talk,
                                &binding_id,
                                recording_binding,
                            ) {
                                PttAction::CancelRelease => {
                                    pending_release = None;
                                    continue;
                                }
                                PttAction::DeferRelease => {
                                    pending_release = Some(PendingRelease {
                                        binding_id,
                                        hotkey_string,
                                        deadline: Instant::now() + RELEASE_GRACE,
                                    });
                                    continue;
                                }
                                PttAction::Passthrough => {}
                            }

                            // Debounce rapid-fire press events (key repeat / double-tap).
                            // Push-to-talk releases may be deferred above to absorb X11 auto-repeat.
                            if is_pressed {
                                let now = Instant::now();
                                if last_press.is_some_and(|t| now.duration_since(t) < DEBOUNCE) {
                                    debug!("Debounced press for '{binding_id}'");
                                    continue;
                                }
                                last_press = Some(now);
                            }

                            if push_to_talk {
                                match classify_ptt_lifecycle(&stage, &binding_id, is_pressed) {
                                    PttLifecycleAction::Start => {
                                        start(&app, &mut stage, &binding_id, &hotkey_string)
                                    }
                                    PttLifecycleAction::Stop => {
                                        stop(&app, &mut stage, &binding_id, &hotkey_string)
                                    }
                                    PttLifecycleAction::Ignore => {}
                                }
                            } else if is_pressed {
                                match &stage {
                                    Stage::Idle => {
                                        start(&app, &mut stage, &binding_id, &hotkey_string);
                                    }
                                    Stage::Recording(id) if id == &binding_id => {
                                        stop(&app, &mut stage, &binding_id, &hotkey_string);
                                    }
                                    _ => {
                                        debug!("Ignoring press for '{binding_id}': pipeline busy")
                                    }
                                }
                            }
                        }
                        Command::Cancel {
                            recording_was_active,
                        } => {
                            pending_release = None;
                            // Don't reset during processing — wait for the pipeline to finish.
                            if !matches!(stage, Stage::Processing)
                                && (recording_was_active || matches!(stage, Stage::Recording(_)))
                            {
                                stage = Stage::Idle;
                            }
                            // Once the stage has settled to idle, restore the armed
                            // legend if the mode is still on. (During Processing the
                            // restore happens on ProcessingFinished instead, after the
                            // pipeline's own teardown has hidden the overlay.)
                            if armed && matches!(stage, Stage::Idle) {
                                crate::overlay::show_armed_overlay(&app);
                            }
                        }
                        Command::ProcessingFinished => {
                            stage = Stage::Idle;
                            if armed {
                                crate::overlay::show_armed_overlay(&app);
                            }
                        }
                        Command::ModePrepare => {
                            if !prepared && !armed {
                                if let Err(error) = crate::overlay::mark_dictation_mode_prepared() {
                                    warn!("Cannot acknowledge prepared dictation mode: {error}");
                                    continue;
                                }
                                prepared = true;
                                debug!("Dictation mode prepared");
                            }
                        }
                        Command::ModeOn => {
                            if prepared && !armed {
                                prepared = false;
                                armed = true;
                                // Show the armed legend without starting a recording.
                                // If work somehow is already active it keeps its own
                                // overlay; the legend returns when that work settles.
                                if matches!(stage, Stage::Idle) {
                                    crate::overlay::show_armed_overlay(&app);
                                }
                                if let Err(error) = crate::overlay::mark_dictation_mode_armed() {
                                    warn!("Cannot acknowledge armed dictation mode: {error}");
                                }
                                debug!("Dictation mode armed");
                            }
                        }
                        Command::ModeOff => {
                            if prepared || armed {
                                let was_armed = armed;
                                prepared = false;
                                armed = false;
                                if let Err(error) = crate::overlay::mark_dictation_mode_off() {
                                    warn!("Cannot acknowledge disarmed dictation mode: {error}");
                                }
                                if was_armed
                                    && matches!(stage, Stage::Recording(_) | Stage::Processing)
                                {
                                    // Cancel active work. The cancellation path hides
                                    // the overlay and notifies us via Command::Cancel;
                                    // with armed=false that notification will not
                                    // re-show the legend.
                                    crate::utils::cancel_current_operation(&app);
                                } else if was_armed {
                                    // Idle: just hide the armed legend.
                                    crate::utils::hide_recording_overlay(&app);
                                }
                                debug!("Dictation mode disarmed");
                            }
                        }
                        Command::ModeSpace { binding_id } => {
                            if !armed {
                                debug!("Ignoring mode Space press: mode off");
                                continue;
                            }
                            let recording_binding = match &stage {
                                Stage::Recording(id) => Some(id.clone()),
                                _ => None,
                            };
                            match classify_mode_space(armed, &stage) {
                                ModeSpaceAction::Start => {
                                    start(&app, &mut stage, &binding_id, MODE_HOTKEY);
                                    if matches!(stage, Stage::Idle) {
                                        // The action hides its recording overlay when microphone
                                        // startup fails. The mode remains armed, so restore its
                                        // persistent indicator immediately.
                                        crate::overlay::show_armed_overlay(&app);
                                    }
                                }
                                ModeSpaceAction::Stop => {
                                    // Stop with the binding that started the recording.
                                    if let Some(rec) = recording_binding {
                                        stop(&app, &mut stage, &rec, MODE_HOTKEY);
                                    }
                                }
                                ModeSpaceAction::Ignore => {
                                    debug!("Ignoring mode Space press: pipeline busy")
                                }
                            }
                        }
                        Command::ModeDelete => {
                            if mode_delete_is_active(armed, &stage) {
                                // Existing cancellation path; it hides the overlay and
                                // notifies us via Command::Cancel, which restores the
                                // armed legend once the stage settles to idle.
                                crate::utils::cancel_current_operation(&app);
                            } else if !armed {
                                debug!("Ignoring mode Delete press: mode off");
                            }
                            // Armed but idle: nothing to cancel, stay armed.
                        }
                    }
                }
                debug!("Transcription coordinator exited");
            }));
            if let Err(e) = result {
                error!("Transcription coordinator panicked: {e:?}");
            }
        });

        Self { tx }
    }

    /// Send a keyboard/signal input event for a transcribe binding.
    /// For signal-based toggles, use `is_pressed: true` and `push_to_talk: false`.
    pub fn send_input(
        &self,
        binding_id: &str,
        hotkey_string: &str,
        is_pressed: bool,
        push_to_talk: bool,
    ) {
        if self
            .tx
            .send(Command::Input {
                binding_id: binding_id.to_string(),
                hotkey_string: hotkey_string.to_string(),
                is_pressed,
                push_to_talk,
            })
            .is_err()
        {
            warn!("Transcription coordinator channel closed");
        }
    }

    pub fn notify_cancel(&self, recording_was_active: bool) {
        if self
            .tx
            .send(Command::Cancel {
                recording_was_active,
            })
            .is_err()
        {
            warn!("Transcription coordinator channel closed");
        }
    }

    pub fn notify_processing_finished(&self) {
        if self.tx.send(Command::ProcessingFinished).is_err() {
            warn!("Transcription coordinator channel closed");
        }
    }

    /// Prepare the visible mode by releasing conflicting Handy shortcuts.
    pub fn mode_prepare(&self) {
        if self.tx.send(Command::ModePrepare).is_err() {
            warn!("Transcription coordinator channel closed");
        }
    }

    /// Commit the prepared visible Space dictation mode.
    pub fn mode_on(&self) {
        if self.tx.send(Command::ModeOn).is_err() {
            warn!("Transcription coordinator channel closed");
        }
    }

    /// Disarm the dictation mode, cancelling any active work.
    pub fn mode_off(&self) {
        if self.tx.send(Command::ModeOff).is_err() {
            warn!("Transcription coordinator channel closed");
        }
    }

    /// A distinct Space press while armed: toggle recording.
    pub fn mode_space(&self, binding_id: &str) {
        if self
            .tx
            .send(Command::ModeSpace {
                binding_id: binding_id.to_string(),
            })
            .is_err()
        {
            warn!("Transcription coordinator channel closed");
        }
    }

    /// A Delete press while armed: cancel active work and stay armed.
    pub fn mode_delete(&self) {
        if self.tx.send(Command::ModeDelete).is_err() {
            warn!("Transcription coordinator channel closed");
        }
    }
}

fn start(app: &AppHandle, stage: &mut Stage, binding_id: &str, hotkey_string: &str) {
    let Some(action) = ACTION_MAP.get(binding_id) else {
        warn!("No action in ACTION_MAP for '{binding_id}'");
        return;
    };
    action.start(app, binding_id, hotkey_string);
    if app
        .try_state::<Arc<AudioRecordingManager>>()
        .is_some_and(|a| a.is_recording())
    {
        *stage = Stage::Recording(binding_id.to_string());
    } else {
        debug!("Start for '{binding_id}' did not begin recording; staying idle");
    }
}

fn stop(app: &AppHandle, stage: &mut Stage, binding_id: &str, hotkey_string: &str) {
    let Some(action) = ACTION_MAP.get(binding_id) else {
        warn!("No action in ACTION_MAP for '{binding_id}'");
        return;
    };
    action.stop(app, binding_id, hotkey_string);
    *stage = Stage::Processing;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn push_to_talk_release_while_recording_defers_release() {
        assert_eq!(
            classify_ptt_event(None, false, true, "transcribe", Some("transcribe")),
            PttAction::DeferRelease
        );
    }

    #[test]
    fn push_to_talk_press_matching_pending_release_cancels_release() {
        assert_eq!(
            classify_ptt_event(
                Some("transcribe"),
                true,
                true,
                "transcribe",
                Some("transcribe")
            ),
            PttAction::CancelRelease
        );
    }

    #[test]
    fn toggle_mode_press_and_release_pass_through() {
        assert_eq!(
            classify_ptt_event(
                Some("transcribe"),
                true,
                false,
                "transcribe",
                Some("transcribe")
            ),
            PttAction::Passthrough
        );
        assert_eq!(
            classify_ptt_event(None, false, false, "transcribe", Some("transcribe")),
            PttAction::Passthrough
        );
    }

    #[test]
    fn press_for_different_binding_than_pending_release_passes_through() {
        assert_eq!(
            classify_ptt_event(
                Some("transcribe"),
                true,
                true,
                "transcribe_with_post_process",
                Some("transcribe")
            ),
            PttAction::Passthrough
        );
    }

    #[test]
    fn press_matching_pending_release_cancels_without_recording_state() {
        assert_eq!(
            classify_ptt_event(Some("transcribe"), true, true, "transcribe", None),
            PttAction::CancelRelease
        );
    }

    #[test]
    fn ptt_press_and_release_are_both_harmless_while_processing() {
        let stage = Stage::Processing;
        assert_eq!(
            classify_ptt_lifecycle(&stage, "transcribe", true),
            PttLifecycleAction::Ignore
        );
        assert_eq!(
            classify_ptt_lifecycle(&stage, "transcribe", false),
            PttLifecycleAction::Ignore
        );
    }

    #[test]
    fn ptt_release_cannot_start_a_recording_from_idle() {
        assert_eq!(
            classify_ptt_lifecycle(&Stage::Idle, "transcribe", false),
            PttLifecycleAction::Ignore
        );
    }

    // ---------------------------------------------------------------------
    // Visible Space dictation-mode classification.
    // ---------------------------------------------------------------------

    #[test]
    fn mode_space_toggles_only_while_armed() {
        assert_eq!(
            classify_mode_space(true, &Stage::Idle),
            ModeSpaceAction::Start
        );
        assert_eq!(
            classify_mode_space(true, &Stage::Recording("transcribe".into())),
            ModeSpaceAction::Stop
        );
        // A disarmed Space press never starts or stops.
        assert_eq!(
            classify_mode_space(false, &Stage::Idle),
            ModeSpaceAction::Ignore
        );
        assert_eq!(
            classify_mode_space(false, &Stage::Recording("transcribe".into())),
            ModeSpaceAction::Ignore
        );
    }

    #[test]
    fn mode_space_is_ignored_while_processing() {
        assert_eq!(
            classify_mode_space(true, &Stage::Processing),
            ModeSpaceAction::Ignore
        );
    }

    #[test]
    fn mode_delete_cancels_only_when_armed_with_active_work() {
        assert!(mode_delete_is_active(
            true,
            &Stage::Recording("transcribe".into())
        ));
        assert!(mode_delete_is_active(true, &Stage::Processing));
        // Armed but idle: nothing to cancel.
        assert!(!mode_delete_is_active(true, &Stage::Idle));
        // Disarmed: never cancels.
        assert!(!mode_delete_is_active(false, &Stage::Recording("t".into())));
        assert!(!mode_delete_is_active(false, &Stage::Processing));
    }

    // ---------------------------------------------------------------------
    // Sequence-level regression coverage for issue #1539.
    //
    // Under X11 key auto-repeat, holding a push-to-talk key does not emit one
    // long press. It emits the initial press followed by a stream of
    // synthesized release/press pairs, then a single genuine release on key-up.
    // Before the fix, every synthesized release passed straight through and
    // stopped recording, so holding the key "rapidly toggled" recording on and
    // off. The fix defers each release for a short grace window and cancels it
    // when the matching auto-repeat press arrives.
    //
    // The unit tests above assert `classify_ptt_event` in isolation. The
    // simulator below threads that classifier through the same `pending_release`
    // / `stage` state transitions the coordinator loop performs (lines that
    // handle `Command::Input` and the `recv_timeout` grace expiry), so a whole
    // event burst can be exercised deterministically without a Tauri AppHandle
    // or real timers.
    // ---------------------------------------------------------------------

    const BINDING: &str = "transcribe";

    #[derive(Clone, Copy)]
    enum Ev {
        /// A key-down event (real initial press or a synthesized auto-repeat press).
        Press,
        /// A key-up event (synthesized auto-repeat release or the genuine key-up).
        Release,
        /// The `RELEASE_GRACE` window elapsed with no cancelling press arriving.
        Grace,
    }

    #[derive(Debug, PartialEq, Eq)]
    enum SimStage {
        Idle,
        Recording,
        Processing,
    }

    struct SimResult {
        starts: u32,
        stops: u32,
        stage: SimStage,
    }

    /// Mirror of the coordinator loop's decision logic for a single push-to-talk
    /// binding: it calls the real `classify_ptt_event` and applies the exact same
    /// Defer / Cancel / debounce / start / stop transitions.
    fn simulate(events: &[Ev]) -> SimResult {
        let mut stage = SimStage::Idle;
        let mut pending: Option<String> = None;
        let mut last_press_ms: Option<u64> = None;
        let mut clock_ms: u64 = 0;
        let mut starts = 0u32;
        let mut stops = 0u32;
        let debounce_ms = DEBOUNCE.as_millis() as u64;

        for ev in events {
            // Auto-repeat events arrive a few ms apart, well inside DEBOUNCE.
            clock_ms += 5;

            match ev {
                Ev::Grace => {
                    // Coordinator's `RecvTimeoutError::Timeout` arm: fire the
                    // deferred release iff we are still recording that binding.
                    if let Some(pending_binding) = pending.take() {
                        if stage == SimStage::Recording && pending_binding == BINDING {
                            stage = SimStage::Processing;
                            stops += 1;
                        }
                    }
                }
                Ev::Press | Ev::Release => {
                    let is_pressed = matches!(ev, Ev::Press);
                    let pending_binding = pending.as_deref();
                    let recording_binding = if stage == SimStage::Recording {
                        Some(BINDING)
                    } else {
                        None
                    };

                    match classify_ptt_event(
                        pending_binding,
                        is_pressed,
                        true, // push_to_talk
                        BINDING,
                        recording_binding,
                    ) {
                        PttAction::CancelRelease => {
                            pending = None;
                            continue;
                        }
                        PttAction::DeferRelease => {
                            pending = Some(BINDING.to_string());
                            continue;
                        }
                        PttAction::Passthrough => {}
                    }

                    if is_pressed {
                        if last_press_ms.is_some_and(|t| clock_ms - t < debounce_ms) {
                            continue;
                        }
                        last_press_ms = Some(clock_ms);
                    }

                    if is_pressed && stage == SimStage::Idle {
                        stage = SimStage::Recording;
                        starts += 1;
                    } else if !is_pressed && stage == SimStage::Recording {
                        stage = SimStage::Processing;
                        stops += 1;
                    }
                }
            }
        }

        SimResult {
            starts,
            stops,
            stage,
        }
    }

    /// Initial press plus several synthesized release/press pairs, as X11 emits
    /// while a push-to-talk key is held down.
    fn autorepeat_burst() -> Vec<Ev> {
        let mut events = vec![Ev::Press];
        for _ in 0..6 {
            events.push(Ev::Release);
            events.push(Ev::Press);
        }
        events
    }

    /// Regression for #1539: a burst of X11 auto-repeat release/press pairs must
    /// not stop recording. Before the fix the first synthesized release stopped
    /// recording immediately (stops == 1, stage left Recording), which produced
    /// the rapid on/off toggling. With the fix the releases are coalesced and
    /// recording stays continuously active for the whole burst.
    #[test]
    fn x11_autorepeat_burst_does_not_toggle_recording() {
        let result = simulate(&autorepeat_burst());
        assert_eq!(result.starts, 1, "recording should start exactly once");
        assert_eq!(
            result.stops, 0,
            "synthesized auto-repeat releases must not stop recording mid-burst"
        );
        assert_eq!(
            result.stage,
            SimStage::Recording,
            "recording must remain active across the entire auto-repeat burst"
        );
    }

    /// Complements the burst test: once the key is genuinely released and the
    /// grace window elapses with no re-press, recording stops exactly once. This
    /// proves the debounce only coalesces synthesized releases and does not wedge
    /// the coordinator or swallow the real key-up.
    #[test]
    fn genuine_release_after_grace_stops_recording_once() {
        let mut events = autorepeat_burst();
        events.push(Ev::Release); // genuine key-up
        events.push(Ev::Grace); // grace window elapses, no cancelling press
        let result = simulate(&events);
        assert_eq!(result.starts, 1, "recording should start exactly once");
        assert_eq!(
            result.stops, 1,
            "a genuine release should stop recording exactly once"
        );
        assert_eq!(result.stage, SimStage::Processing);
    }
}
