use crate::actions::{
    finish_remote_operation, start_remote_operation, RemoteDeliveryMode, RemoteDeliveryPlan,
    RemoteOperationPlan, ACTION_MAP,
};
use crate::clipboard::RemoteInjectionPlan;
use crate::managers::audio::AudioRecordingManager;
use crate::operation::{OperationOutcome, OperationOwner};
use log::{debug, error, warn};
use std::collections::VecDeque;
use std::sync::mpsc::{self, Sender};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Manager};

const DEBOUNCE: Duration = Duration::from_millis(30);
const RELEASE_GRACE: Duration = Duration::from_millis(50);
const REMOTE_REPLY_TIMEOUT: Duration = Duration::from_secs(10);
pub(crate) const REMOTE_REQUEST_LIFETIME: Duration = Duration::from_secs(10 * 60);
pub(crate) const REMOTE_MAX_AUDIO_CHUNK_SAMPLES: usize = 4_800;
pub(crate) const REMOTE_MAX_TOTAL_AUDIO_SAMPLES: usize = 16_000 * 60 * 10;
const REMOTE_TERMINAL_LIFETIME: Duration = Duration::from_secs(60);
const MAX_REMOTE_TERMINALS: usize = 8;

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

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum RemoteStatus {
    Recording,
    Processing,
    Ready,
    Cancelling,
    Succeeded,
    Failed,
    Cancelled,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum RemoteCancelStatus {
    Cancelled,
    Cancelling,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct RemoteCommitOutcome {
    pub(crate) status: RemoteStatus,
    pub(crate) injection: Option<RemoteInjectionPlan>,
}

struct RecordingRemote {
    request_id: String,
    connection_id: u64,
    request_deadline: Instant,
    total_audio_samples: usize,
    plan: RemoteOperationPlan,
}

struct ProcessingRemote {
    request_id: String,
    connection_id: u64,
    request_deadline: Instant,
    cancelled: bool,
    delivery: RemoteDeliveryPlan,
}

struct ReadyRemote {
    request_id: String,
    connection_id: u64,
    request_deadline: Instant,
    text: String,
    delivery: RemoteDeliveryPlan,
}

enum ActiveRemote {
    Recording(RecordingRemote),
    Processing(ProcessingRemote),
    Ready(ReadyRemote),
}

impl ActiveRemote {
    fn request_id(&self) -> &str {
        match self {
            Self::Recording(remote) => &remote.request_id,
            Self::Processing(remote) => &remote.request_id,
            Self::Ready(remote) => &remote.request_id,
        }
    }

    fn connection_id(&self) -> u64 {
        match self {
            Self::Recording(remote) => remote.connection_id,
            Self::Processing(remote) => remote.connection_id,
            Self::Ready(remote) => remote.connection_id,
        }
    }
}

struct RemoteTerminal {
    request_id: String,
    connection_id: u64,
    status: RemoteStatus,
    expires_at: Instant,
}

/// Commands processed sequentially by the coordinator thread.
enum Command {
    Input {
        binding_id: String,
        hotkey_string: String,
        is_pressed: bool,
        push_to_talk: bool,
    },
    LocalCancel,
    ProcessingFinished {
        owner: OperationOwner,
        outcome: OperationOutcome,
    },
    // Visible Space dictation mode (qq-dictation). Right-Control arms/exits;
    // while armed, Space toggles recording and Delete cancels active local work.
    ModePrepare,
    ModeOn,
    ModeOff,
    ModeSpace {
        binding_id: String,
    },
    ModeDelete,
    RemoteStart {
        connection_id: u64,
        request_id: String,
        delivery_mode: RemoteDeliveryMode,
        reply: Sender<Result<(), String>>,
    },
    RemoteAudio {
        connection_id: u64,
        request_id: String,
        pcm: Vec<i16>,
        reply: Sender<Result<(), String>>,
    },
    RemoteFinish {
        connection_id: u64,
        request_id: String,
        reply: Sender<Result<(), String>>,
    },
    RemoteReady {
        request_id: String,
        text: String,
        reply: Sender<Result<(), String>>,
    },
    RemoteCommit {
        connection_id: u64,
        request_id: String,
        reply: Sender<Result<RemoteCommitOutcome, String>>,
    },
    RemoteCancel {
        connection_id: u64,
        request_id: String,
        reply: Sender<Result<RemoteCancelStatus, String>>,
    },
    RemoteStatus {
        connection_id: u64,
        request_id: String,
        reply: Sender<Result<RemoteStatus, String>>,
    },
    RemoteDisconnect {
        connection_id: u64,
    },
    RemoteTick,
}

/// Pipeline lifecycle, owned exclusively by the coordinator thread.
#[derive(Clone, Debug, PartialEq, Eq)]
enum Stage {
    Idle,
    Recording(OperationOwner),
    Processing(OperationOwner),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum PttLifecycleAction {
    Start,
    Stop,
    Ignore,
}

fn local_binding_matches(owner: &OperationOwner, binding_id: &str) -> bool {
    matches!(owner, OperationOwner::Local { binding_id: active } if active == binding_id)
}

fn classify_ptt_lifecycle(stage: &Stage, binding_id: &str, is_pressed: bool) -> PttLifecycleAction {
    match (stage, is_pressed) {
        (Stage::Idle, true) => PttLifecycleAction::Start,
        (Stage::Recording(owner), false) if local_binding_matches(owner, binding_id) => {
            PttLifecycleAction::Stop
        }
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
        Stage::Recording(owner) if owner.is_local() => ModeSpaceAction::Stop,
        // Local controls never stop a remote owner, and presses while any
        // transcript is in flight never invert into a later start.
        Stage::Recording(_) | Stage::Processing(_) => ModeSpaceAction::Ignore,
    }
}

/// Delete cancels only when the mode is armed and local work owns the pipeline.
fn mode_delete_is_active(armed: bool, stage: &Stage) -> bool {
    armed
        && matches!(
            stage,
            Stage::Recording(owner) | Stage::Processing(owner) if owner.is_local()
        )
}

fn active_local_binding(stage: &Stage) -> Option<&str> {
    match stage {
        Stage::Recording(OperationOwner::Local { binding_id }) => Some(binding_id),
        _ => None,
    }
}

fn remote_matches(remote: &ActiveRemote, connection_id: u64, request_id: &str) -> bool {
    remote.connection_id() == connection_id && remote.request_id() == request_id
}

fn active_remote_status(remote: &ActiveRemote) -> RemoteStatus {
    match remote {
        ActiveRemote::Recording(_) => RemoteStatus::Recording,
        ActiveRemote::Processing(remote) if remote.cancelled => RemoteStatus::Cancelling,
        ActiveRemote::Processing(_) => RemoteStatus::Processing,
        ActiveRemote::Ready(_) => RemoteStatus::Ready,
    }
}

fn processing_finish_status(remote: &ProcessingRemote, outcome: OperationOutcome) -> RemoteStatus {
    if remote.cancelled {
        RemoteStatus::Cancelled
    } else {
        finish_status(outcome)
    }
}

fn stage_ready_state(
    stage: &Stage,
    active_remote: &mut Option<ActiveRemote>,
    request_id: &str,
    text: String,
) -> Result<(), String> {
    match active_remote.take() {
        Some(ActiveRemote::Processing(remote))
            if remote.request_id == request_id
                && !remote.cancelled
                && matches!(
                    stage,
                    Stage::Processing(owner)
                        if owner.remote_request_id() == Some(request_id)
                ) =>
        {
            if text.trim().is_empty() {
                *active_remote = Some(ActiveRemote::Processing(remote));
                Err("Blank remote output cannot enter ready state".to_string())
            } else if matches!(&remote.delivery, RemoteDeliveryPlan::Local)
                && crate::clipboard::validate_remote_local_transcript(&text).is_err()
            {
                *active_remote = Some(ActiveRemote::Processing(remote));
                Err("Remote local-injection text is outside bounds".to_string())
            } else {
                *active_remote = Some(ActiveRemote::Ready(ReadyRemote {
                    request_id: remote.request_id,
                    connection_id: remote.connection_id,
                    request_deadline: remote.request_deadline,
                    text,
                    delivery: remote.delivery,
                }));
                Ok(())
            }
        }
        Some(other) => {
            *active_remote = Some(other);
            Err("Remote ready result is stale or cancellation-owned".to_string())
        }
        None => Err("No remote request can become ready".to_string()),
    }
}

fn take_ready_for_commit(
    active_remote: &mut Option<ActiveRemote>,
    connection_id: u64,
    request_id: &str,
) -> Result<ReadyRemote, String> {
    match active_remote.take() {
        Some(ActiveRemote::Ready(remote))
            if remote.connection_id == connection_id && remote.request_id == request_id =>
        {
            Ok(remote)
        }
        Some(other) => {
            *active_remote = Some(other);
            Err("Commit is early, replayed, or not request owner".to_string())
        }
        None => Err("No ready remote request exists".to_string()),
    }
}

fn remote_slot_available(stage: &Stage, active_remote: &Option<ActiveRemote>) -> bool {
    matches!(stage, Stage::Idle) && active_remote.is_none()
}

fn next_remote_audio_total(current: usize, chunk: usize) -> Result<usize, String> {
    if chunk == 0 || chunk > REMOTE_MAX_AUDIO_CHUNK_SAMPLES {
        return Err("Remote audio chunk length is outside bounds".to_string());
    }
    current
        .checked_add(chunk)
        .filter(|total| *total <= REMOTE_MAX_TOTAL_AUDIO_SAMPLES)
        .ok_or_else(|| "Remote request audio limit exceeded".to_string())
}

fn push_terminal(
    terminals: &mut VecDeque<RemoteTerminal>,
    request_id: String,
    connection_id: u64,
    status: RemoteStatus,
    now: Instant,
) {
    terminals.retain(|terminal| terminal.expires_at > now);
    terminals.push_back(RemoteTerminal {
        request_id,
        connection_id,
        status,
        expires_at: now + REMOTE_TERMINAL_LIFETIME,
    });
    while terminals.len() > MAX_REMOTE_TERMINALS {
        terminals.pop_front();
    }
}

fn terminal_status(
    terminals: &mut VecDeque<RemoteTerminal>,
    connection_id: u64,
    request_id: &str,
    now: Instant,
) -> Option<RemoteStatus> {
    terminals.retain(|terminal| terminal.expires_at > now);
    terminals
        .iter()
        .find(|terminal| {
            terminal.connection_id == connection_id && terminal.request_id == request_id
        })
        .map(|terminal| terminal.status.clone())
}

fn finish_status(outcome: OperationOutcome) -> RemoteStatus {
    match outcome {
        OperationOutcome::Succeeded => RemoteStatus::Succeeded,
        OperationOutcome::Failed => RemoteStatus::Failed,
        OperationOutcome::Cancelled => RemoteStatus::Cancelled,
    }
}

fn commit_delivery_status(deliver: impl FnOnce() -> Result<(), String>) -> RemoteStatus {
    match deliver() {
        Ok(()) => RemoteStatus::Succeeded,
        Err(error) => {
            error!("Remote commit delivery failed: {error}");
            RemoteStatus::Failed
        }
    }
}

fn expire_remote(
    app: &AppHandle,
    stage: &mut Stage,
    active_remote: &mut Option<ActiveRemote>,
    terminals: &mut VecDeque<RemoteTerminal>,
    now: Instant,
) {
    let expired = match active_remote.as_ref() {
        Some(ActiveRemote::Recording(remote)) => now >= remote.request_deadline,
        Some(ActiveRemote::Processing(remote)) => {
            now >= remote.request_deadline && !remote.cancelled
        }
        Some(ActiveRemote::Ready(remote)) => now >= remote.request_deadline,
        None => false,
    };
    if !expired {
        return;
    }

    match active_remote.as_mut() {
        Some(ActiveRemote::Recording(remote)) => {
            warn!("Remote recording request {} expired", remote.request_id);
            let owner = OperationOwner::remote(&remote.request_id);
            crate::utils::cancel_owned_operation(app, &owner, true);
            push_terminal(
                terminals,
                remote.request_id.clone(),
                remote.connection_id,
                RemoteStatus::Cancelled,
                now,
            );
            *active_remote = None;
            *stage = Stage::Idle;
        }
        Some(ActiveRemote::Processing(remote)) => {
            warn!("Remote processing request {} expired", remote.request_id);
            let owner = OperationOwner::remote(&remote.request_id);
            crate::utils::cancel_owned_operation(app, &owner, false);
            remote.cancelled = true;
        }
        Some(ActiveRemote::Ready(remote)) => {
            warn!(
                "Remote ready request {} expired before commit",
                remote.request_id
            );
            push_terminal(
                terminals,
                remote.request_id.clone(),
                remote.connection_id,
                RemoteStatus::Cancelled,
                now,
            );
            *active_remote = None;
            *stage = Stage::Idle;
            crate::utils::hide_recording_overlay(app);
            crate::tray::change_tray_icon(app, crate::tray::TrayIconState::Idle);
        }
        None => {}
    }
}

/// Serialises all transcription lifecycle events through a single thread to
/// eliminate races between local controls, remote protocol messages, and the
/// async transcribe/delivery pipeline.
pub struct TranscriptionCoordinator {
    tx: Sender<Command>,
}

pub fn is_transcribe_binding(id: &str) -> bool {
    id == "transcribe"
}

impl TranscriptionCoordinator {
    pub fn new(app: AppHandle) -> Self {
        let (tx, rx) = mpsc::channel();

        thread::spawn(move || {
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                let mut stage = Stage::Idle;
                let mut last_press: Option<Instant> = None;
                let mut pending_release: Option<PendingRelease> = None;
                let mut active_remote: Option<ActiveRemote> = None;
                let mut terminals = VecDeque::new();
                // Visible Space dictation mode defaults off on every app start.
                let mut prepared = false;
                let mut armed = false;

                loop {
                    let command = if let Some(pending) = &pending_release {
                        match rx.recv_timeout(
                            pending.deadline.saturating_duration_since(Instant::now()),
                        ) {
                            Ok(command) => command,
                            Err(mpsc::RecvTimeoutError::Timeout) => {
                                if let Some(pending) = pending_release.take() {
                                    if active_local_binding(&stage)
                                        == Some(pending.binding_id.as_str())
                                    {
                                        stop_local(
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
                            Ok(command) => command,
                            Err(_) => break,
                        }
                    };

                    match command {
                        Command::Input {
                            binding_id,
                            hotkey_string,
                            is_pressed,
                            push_to_talk,
                        } => {
                            let pending_release_binding = pending_release
                                .as_ref()
                                .map(|pending| pending.binding_id.as_str());
                            let recording_binding = active_local_binding(&stage);

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

                            if is_pressed {
                                let now = Instant::now();
                                if last_press
                                    .is_some_and(|last| now.duration_since(last) < DEBOUNCE)
                                {
                                    debug!("Debounced press for '{binding_id}'");
                                    continue;
                                }
                                last_press = Some(now);
                            }

                            if push_to_talk {
                                match classify_ptt_lifecycle(&stage, &binding_id, is_pressed) {
                                    PttLifecycleAction::Start => {
                                        start_local(&app, &mut stage, &binding_id, &hotkey_string)
                                    }
                                    PttLifecycleAction::Stop => {
                                        stop_local(&app, &mut stage, &binding_id, &hotkey_string)
                                    }
                                    PttLifecycleAction::Ignore => {}
                                }
                            } else if is_pressed {
                                match &stage {
                                    Stage::Idle => {
                                        start_local(&app, &mut stage, &binding_id, &hotkey_string)
                                    }
                                    Stage::Recording(owner)
                                        if local_binding_matches(owner, &binding_id) =>
                                    {
                                        stop_local(&app, &mut stage, &binding_id, &hotkey_string)
                                    }
                                    _ => debug!(
                                        "Ignoring press for '{binding_id}': another owner is busy"
                                    ),
                                }
                            }
                        }
                        Command::LocalCancel => {
                            pending_release = None;
                            match &stage {
                                Stage::Recording(owner) if owner.is_local() => {
                                    crate::utils::cancel_owned_operation(&app, owner, true);
                                    stage = Stage::Idle;
                                }
                                Stage::Processing(owner) if owner.is_local() => {
                                    crate::utils::cancel_owned_operation(&app, owner, false);
                                }
                                _ => debug!("Ignoring local cancel: local source is not owner"),
                            }
                            if armed && matches!(stage, Stage::Idle) {
                                crate::overlay::show_armed_overlay(&app);
                            }
                        }
                        Command::ProcessingFinished { owner, outcome } => {
                            if matches!(&stage, Stage::Processing(active) if active == &owner) {
                                if let Some(remote_state) = active_remote.take() {
                                    match remote_state {
                                        ActiveRemote::Processing(remote)
                                            if owner.remote_request_id()
                                                == Some(&remote.request_id) =>
                                        {
                                            let status = processing_finish_status(&remote, outcome);
                                            push_terminal(
                                                &mut terminals,
                                                remote.request_id,
                                                remote.connection_id,
                                                status,
                                                Instant::now(),
                                            );
                                        }
                                        other => {
                                            active_remote = Some(other);
                                            warn!(
                                                "Ignoring completion that does not own active remote state"
                                            );
                                            continue;
                                        }
                                    }
                                }
                                stage = Stage::Idle;
                                if armed {
                                    crate::overlay::show_armed_overlay(&app);
                                }
                            } else {
                                warn!("Ignoring completion from non-owner {owner}");
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
                                match &stage {
                                    Stage::Recording(owner) if owner.is_local() => {
                                        crate::utils::cancel_owned_operation(&app, owner, true);
                                        stage = Stage::Idle;
                                    }
                                    Stage::Processing(owner) if owner.is_local() => {
                                        crate::utils::cancel_owned_operation(&app, owner, false);
                                    }
                                    _ if was_armed && matches!(stage, Stage::Idle) => {
                                        crate::utils::hide_recording_overlay(&app);
                                    }
                                    _ => {}
                                }
                                debug!("Dictation mode disarmed");
                            }
                        }
                        Command::ModeSpace { binding_id } => {
                            let recording_binding =
                                active_local_binding(&stage).map(str::to_string);
                            match classify_mode_space(armed, &stage) {
                                ModeSpaceAction::Start => {
                                    start_local(&app, &mut stage, &binding_id, MODE_HOTKEY);
                                    if matches!(stage, Stage::Idle) {
                                        crate::overlay::show_armed_overlay(&app);
                                    }
                                }
                                ModeSpaceAction::Stop => {
                                    if let Some(binding_id) = recording_binding {
                                        stop_local(&app, &mut stage, &binding_id, MODE_HOTKEY);
                                    }
                                }
                                ModeSpaceAction::Ignore => {
                                    debug!("Ignoring mode Space press: mode off or pipeline busy")
                                }
                            }
                        }
                        Command::ModeDelete => {
                            if mode_delete_is_active(armed, &stage) {
                                if let Stage::Recording(owner) = &stage {
                                    crate::utils::cancel_owned_operation(&app, owner, true);
                                    stage = Stage::Idle;
                                    crate::overlay::show_armed_overlay(&app);
                                } else if let Stage::Processing(owner) = &stage {
                                    crate::utils::cancel_owned_operation(&app, owner, false);
                                }
                            } else {
                                debug!("Ignoring mode Delete: local source is not active owner");
                            }
                        }
                        Command::RemoteStart {
                            connection_id,
                            request_id,
                            delivery_mode,
                            reply,
                        } => {
                            let now = Instant::now();
                            expire_remote(
                                &app,
                                &mut stage,
                                &mut active_remote,
                                &mut terminals,
                                now,
                            );
                            let reused = active_remote
                                .as_ref()
                                .is_some_and(|remote| remote.request_id() == request_id)
                                || terminals
                                    .iter()
                                    .any(|terminal| terminal.request_id == request_id);
                            let result = if reused {
                                Err("Remote request id was replayed".to_string())
                            } else if !remote_slot_available(&stage, &active_remote) {
                                Err("Dictation pipeline is owned by another source".to_string())
                            } else {
                                match start_remote_operation(&app, &request_id, delivery_mode) {
                                    Ok(plan) => {
                                        stage =
                                            Stage::Recording(OperationOwner::remote(&request_id));
                                        active_remote =
                                            Some(ActiveRemote::Recording(RecordingRemote {
                                                request_id,
                                                connection_id,
                                                request_deadline: now + REMOTE_REQUEST_LIFETIME,
                                                total_audio_samples: 0,
                                                plan,
                                            }));
                                        Ok(())
                                    }
                                    Err(error) => Err(error),
                                }
                            };
                            let _ = reply.send(result);
                        }
                        Command::RemoteAudio {
                            connection_id,
                            request_id,
                            pcm,
                            reply,
                        } => {
                            let now = Instant::now();
                            expire_remote(
                                &app,
                                &mut stage,
                                &mut active_remote,
                                &mut terminals,
                                now,
                            );
                            let result =
                                match active_remote.as_mut() {
                                    Some(ActiveRemote::Recording(remote))
                                        if remote.connection_id == connection_id
                                            && remote.request_id == request_id =>
                                    {
                                        match next_remote_audio_total(
                                            remote.total_audio_samples,
                                            pcm.len(),
                                        ) {
                                            Ok(next_total) => match app
                                                .state::<Arc<AudioRecordingManager>>()
                                                .feed_remote_pcm(&request_id, &pcm)
                                            {
                                                Ok(()) => {
                                                    remote.total_audio_samples = next_total;
                                                    Ok(())
                                                }
                                                Err(error) => Err(error),
                                            },
                                            Err(error) => Err(error),
                                        }
                                    }
                                    _ => Err("Audio is out of order or the request is not owner"
                                        .to_string()),
                                };
                            let _ = reply.send(result);
                        }
                        Command::RemoteFinish {
                            connection_id,
                            request_id,
                            reply,
                        } => {
                            let now = Instant::now();
                            expire_remote(
                                &app,
                                &mut stage,
                                &mut active_remote,
                                &mut terminals,
                                now,
                            );
                            let result = match active_remote.take() {
                                Some(ActiveRemote::Recording(remote))
                                    if remote.connection_id == connection_id
                                        && remote.request_id == request_id =>
                                {
                                    if remote.total_audio_samples == 0 {
                                        active_remote = Some(ActiveRemote::Recording(remote));
                                        Err("Cannot finish before audio is accepted".to_string())
                                    } else {
                                        let owner = OperationOwner::remote(&request_id);
                                        stage = Stage::Processing(owner);
                                        let processing = ProcessingRemote {
                                            request_id: request_id.clone(),
                                            connection_id,
                                            request_deadline: remote.request_deadline,
                                            cancelled: false,
                                            delivery: remote.plan.delivery.clone(),
                                        };
                                        finish_remote_operation(&app, &request_id, remote.plan);
                                        active_remote = Some(ActiveRemote::Processing(processing));
                                        Ok(())
                                    }
                                }
                                Some(other) => {
                                    active_remote = Some(other);
                                    Err("Finish is out of order or the request is not owner"
                                        .to_string())
                                }
                                None => Err("No remote request is active".to_string()),
                            };
                            let _ = reply.send(result);
                        }
                        Command::RemoteReady {
                            request_id,
                            text,
                            reply,
                        } => {
                            let result =
                                stage_ready_state(&stage, &mut active_remote, &request_id, text);
                            let _ = reply.send(result);
                        }
                        Command::RemoteCommit {
                            connection_id,
                            request_id,
                            reply,
                        } => {
                            let now = Instant::now();
                            expire_remote(
                                &app,
                                &mut stage,
                                &mut active_remote,
                                &mut terminals,
                                now,
                            );
                            let result = take_ready_for_commit(
                                &mut active_remote,
                                connection_id,
                                &request_id,
                            )
                            .map(|remote| {
                                // Taking Ready serializes this one consuming handoff against
                                // disconnect, cancel, cross-owner access, and replay.
                                let outcome = match remote.delivery {
                                    RemoteDeliveryPlan::Herdr(herdr_identity) => {
                                        // Herdr keeps its existing effectful identity recheck and
                                        // one explicit pane-send boundary on the workstation.
                                        let status = commit_delivery_status(|| {
                                            crate::clipboard::paste_remote_commit(
                                                remote.text,
                                                &herdr_identity,
                                                app.clone(),
                                            )
                                        });
                                        RemoteCommitOutcome {
                                            status,
                                            injection: None,
                                        }
                                    }
                                    RemoteDeliveryPlan::Local => {
                                        // Laptop-local commit consumes Ready before constructing
                                        // the bounded workstation-authored injection plan. The
                                        // laptop owns the one later effect attempt.
                                        match crate::clipboard::prepare_remote_injection_plan(
                                            remote.text,
                                            &app,
                                        ) {
                                            Ok(injection) => RemoteCommitOutcome {
                                                status: RemoteStatus::Succeeded,
                                                injection: Some(injection),
                                            },
                                            Err(error) => {
                                                error!("Remote local handoff failed: {error}");
                                                RemoteCommitOutcome {
                                                    status: RemoteStatus::Failed,
                                                    injection: None,
                                                }
                                            }
                                        }
                                    }
                                };
                                push_terminal(
                                    &mut terminals,
                                    remote.request_id,
                                    remote.connection_id,
                                    outcome.status.clone(),
                                    Instant::now(),
                                );
                                stage = Stage::Idle;
                                crate::utils::hide_recording_overlay(&app);
                                crate::tray::change_tray_icon(
                                    &app,
                                    crate::tray::TrayIconState::Idle,
                                );
                                if armed {
                                    crate::overlay::show_armed_overlay(&app);
                                }
                                outcome
                            });
                            let _ = reply.send(result);
                        }
                        Command::RemoteCancel {
                            connection_id,
                            request_id,
                            reply,
                        } => {
                            let result = cancel_remote(
                                &app,
                                &mut stage,
                                &mut active_remote,
                                connection_id,
                                &request_id,
                            );
                            let _ = reply.send(result);
                        }
                        Command::RemoteStatus {
                            connection_id,
                            request_id,
                            reply,
                        } => {
                            let now = Instant::now();
                            expire_remote(
                                &app,
                                &mut stage,
                                &mut active_remote,
                                &mut terminals,
                                now,
                            );
                            let result = match active_remote.as_ref() {
                                Some(remote)
                                    if remote_matches(remote, connection_id, &request_id) =>
                                {
                                    Ok(active_remote_status(remote))
                                }
                                _ => {
                                    terminal_status(&mut terminals, connection_id, &request_id, now)
                                        .ok_or_else(|| {
                                            "Remote request id is stale or not owner".to_string()
                                        })
                                }
                            };
                            let _ = reply.send(result);
                        }
                        Command::RemoteDisconnect { connection_id } => {
                            if let Some(remote) = active_remote.as_ref() {
                                if remote.connection_id() == connection_id {
                                    let request_id = remote.request_id().to_string();
                                    let _ = cancel_remote(
                                        &app,
                                        &mut stage,
                                        &mut active_remote,
                                        connection_id,
                                        &request_id,
                                    );
                                }
                            }
                        }
                        Command::RemoteTick => {
                            expire_remote(
                                &app,
                                &mut stage,
                                &mut active_remote,
                                &mut terminals,
                                Instant::now(),
                            );
                        }
                    }
                }
                debug!("Transcription coordinator exited");
            }));
            if let Err(error) = result {
                error!("Transcription coordinator panicked: {error:?}");
            }
        });

        Self { tx }
    }

    fn request<T>(
        &self,
        build: impl FnOnce(Sender<Result<T, String>>) -> Command,
    ) -> Result<T, String> {
        let (reply_tx, reply_rx) = mpsc::channel();
        self.tx
            .send(build(reply_tx))
            .map_err(|_| "Transcription coordinator channel closed".to_string())?;
        reply_rx
            .recv_timeout(REMOTE_REPLY_TIMEOUT)
            .map_err(|_| "Transcription coordinator reply timed out".to_string())?
    }

    /// Send a keyboard/signal input event for a transcribe binding.
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

    pub(crate) fn request_local_cancel(&self) {
        if self.tx.send(Command::LocalCancel).is_err() {
            warn!("Transcription coordinator channel closed");
        }
    }

    pub(crate) fn notify_processing_finished(
        &self,
        owner: OperationOwner,
        outcome: OperationOutcome,
    ) {
        if self
            .tx
            .send(Command::ProcessingFinished { owner, outcome })
            .is_err()
        {
            warn!("Transcription coordinator channel closed");
        }
    }

    pub(crate) fn remote_start(
        &self,
        connection_id: u64,
        request_id: String,
        delivery_mode: RemoteDeliveryMode,
    ) -> Result<(), String> {
        self.request(|reply| Command::RemoteStart {
            connection_id,
            request_id,
            delivery_mode,
            reply,
        })
    }

    pub(crate) fn remote_audio(
        &self,
        connection_id: u64,
        request_id: String,
        pcm: Vec<i16>,
    ) -> Result<(), String> {
        self.request(|reply| Command::RemoteAudio {
            connection_id,
            request_id,
            pcm,
            reply,
        })
    }

    pub(crate) fn remote_finish(
        &self,
        connection_id: u64,
        request_id: String,
    ) -> Result<(), String> {
        self.request(|reply| Command::RemoteFinish {
            connection_id,
            request_id,
            reply,
        })
    }

    pub(crate) fn stage_remote_delivery(
        &self,
        request_id: String,
        text: String,
    ) -> Result<(), String> {
        self.request(|reply| Command::RemoteReady {
            request_id,
            text,
            reply,
        })
    }

    pub(crate) fn remote_commit(
        &self,
        connection_id: u64,
        request_id: String,
    ) -> Result<RemoteCommitOutcome, String> {
        self.request(|reply| Command::RemoteCommit {
            connection_id,
            request_id,
            reply,
        })
    }

    pub(crate) fn remote_cancel(
        &self,
        connection_id: u64,
        request_id: String,
    ) -> Result<RemoteCancelStatus, String> {
        self.request(|reply| Command::RemoteCancel {
            connection_id,
            request_id,
            reply,
        })
    }

    pub(crate) fn remote_status(
        &self,
        connection_id: u64,
        request_id: String,
    ) -> Result<RemoteStatus, String> {
        self.request(|reply| Command::RemoteStatus {
            connection_id,
            request_id,
            reply,
        })
    }

    pub(crate) fn remote_disconnect(&self, connection_id: u64) {
        let _ = self.tx.send(Command::RemoteDisconnect { connection_id });
    }

    pub(crate) fn remote_tick(&self) {
        let _ = self.tx.send(Command::RemoteTick);
    }

    pub fn mode_prepare(&self) {
        let _ = self.tx.send(Command::ModePrepare);
    }

    pub fn mode_on(&self) {
        let _ = self.tx.send(Command::ModeOn);
    }

    pub fn mode_off(&self) {
        let _ = self.tx.send(Command::ModeOff);
    }

    pub fn mode_space(&self, binding_id: &str) {
        let _ = self.tx.send(Command::ModeSpace {
            binding_id: binding_id.to_string(),
        });
    }

    pub fn mode_delete(&self) {
        let _ = self.tx.send(Command::ModeDelete);
    }
}

fn cancel_remote(
    app: &AppHandle,
    stage: &mut Stage,
    active_remote: &mut Option<ActiveRemote>,
    connection_id: u64,
    request_id: &str,
) -> Result<RemoteCancelStatus, String> {
    let Some(remote) = active_remote.as_mut() else {
        return Err("No remote request is active".to_string());
    };
    if !remote_matches(remote, connection_id, request_id) {
        return Err("Remote cancel request is not owner".to_string());
    }

    match remote {
        ActiveRemote::Recording(remote) => {
            let owner = OperationOwner::remote(&remote.request_id);
            crate::utils::cancel_owned_operation(app, &owner, true);
            *active_remote = None;
            *stage = Stage::Idle;
            Ok(RemoteCancelStatus::Cancelled)
        }
        ActiveRemote::Processing(remote) if remote.cancelled => {
            Err("Remote request is already cancelling".to_string())
        }
        ActiveRemote::Processing(remote) => {
            let owner = OperationOwner::remote(&remote.request_id);
            crate::utils::cancel_owned_operation(app, &owner, false);
            remote.cancelled = true;
            Ok(RemoteCancelStatus::Cancelling)
        }
        ActiveRemote::Ready(_) => {
            *active_remote = None;
            *stage = Stage::Idle;
            crate::utils::hide_recording_overlay(app);
            crate::tray::change_tray_icon(app, crate::tray::TrayIconState::Idle);
            Ok(RemoteCancelStatus::Cancelled)
        }
    }
}

fn start_local(app: &AppHandle, stage: &mut Stage, binding_id: &str, hotkey_string: &str) {
    let Some(action) = ACTION_MAP.get(binding_id) else {
        warn!("No action in ACTION_MAP for '{binding_id}'");
        return;
    };
    action.start(app, binding_id, hotkey_string);
    let expected = OperationOwner::local(binding_id);
    if app
        .try_state::<Arc<AudioRecordingManager>>()
        .and_then(|audio| audio.active_owner())
        .as_ref()
        == Some(&expected)
    {
        *stage = Stage::Recording(expected);
    } else {
        debug!("Start for '{binding_id}' did not begin recording; staying idle");
    }
}

fn stop_local(app: &AppHandle, stage: &mut Stage, binding_id: &str, hotkey_string: &str) {
    let owner = OperationOwner::local(binding_id);
    if !matches!(stage, Stage::Recording(active) if active == &owner) {
        debug!("Ignoring stop from non-owner {owner}");
        return;
    }
    let Some(action) = ACTION_MAP.get(binding_id) else {
        warn!("No action in ACTION_MAP for '{binding_id}'");
        return;
    };
    action.stop(app, binding_id, hotkey_string);
    *stage = Stage::Processing(owner);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn local_recording(binding_id: &str) -> Stage {
        Stage::Recording(OperationOwner::local(binding_id))
    }

    fn remote_recording(request_id: &str) -> Stage {
        Stage::Recording(OperationOwner::remote(request_id))
    }

    #[test]
    fn push_to_talk_release_while_local_recording_defers_release() {
        assert_eq!(
            classify_ptt_event(None, false, true, "transcribe", Some("transcribe")),
            PttAction::DeferRelease
        );
    }

    #[test]
    fn ptt_controls_cannot_stop_remote_or_processing_work() {
        assert_eq!(
            classify_ptt_lifecycle(&remote_recording("request-a"), "transcribe", false),
            PttLifecycleAction::Ignore
        );
        assert_eq!(
            classify_ptt_lifecycle(
                &Stage::Processing(OperationOwner::remote("request-a")),
                "transcribe",
                true,
            ),
            PttLifecycleAction::Ignore
        );
        assert_eq!(
            classify_ptt_lifecycle(&Stage::Idle, "transcribe", false),
            PttLifecycleAction::Ignore
        );
    }

    #[test]
    fn pending_release_press_is_cancelled() {
        assert_eq!(
            classify_ptt_event(
                Some("transcribe"),
                true,
                true,
                "transcribe",
                Some("transcribe"),
            ),
            PttAction::CancelRelease
        );
    }

    #[test]
    fn mode_space_and_delete_apply_only_to_local_owner() {
        assert_eq!(
            classify_mode_space(true, &Stage::Idle),
            ModeSpaceAction::Start
        );
        assert_eq!(
            classify_mode_space(true, &local_recording("transcribe")),
            ModeSpaceAction::Stop
        );
        assert_eq!(
            classify_mode_space(true, &remote_recording("request-a")),
            ModeSpaceAction::Ignore
        );
        assert!(mode_delete_is_active(
            true,
            &Stage::Processing(OperationOwner::local("transcribe"))
        ));
        assert!(!mode_delete_is_active(
            true,
            &Stage::Processing(OperationOwner::remote("request-a"))
        ));
    }

    #[test]
    fn remote_start_acquires_recording_slot_without_a_target_state() {
        let mut stage = Stage::Idle;
        let mut active = None;
        assert!(remote_slot_available(&stage, &active));

        stage = Stage::Recording(OperationOwner::remote("request-a"));
        active = Some(ActiveRemote::Recording(RecordingRemote {
            request_id: "request-a".into(),
            connection_id: 7,
            request_deadline: Instant::now() + REMOTE_REQUEST_LIFETIME,
            total_audio_samples: 0,
            plan: RemoteOperationPlan {
                delivery: RemoteDeliveryPlan::Herdr(
                    crate::target_binding::synthetic_remote_session_identity(1),
                ),
            },
        }));
        assert!(!remote_slot_available(&stage, &active));
        assert_eq!(
            active_remote_status(active.as_ref().unwrap()),
            RemoteStatus::Recording
        );
    }

    #[test]
    fn processing_cancel_stays_busy_until_completion_records_cancelled() {
        let mut stage = Stage::Processing(OperationOwner::remote("request-a"));
        let mut active = Some(ActiveRemote::Processing(ProcessingRemote {
            request_id: "request-a".into(),
            connection_id: 7,
            request_deadline: Instant::now() + REMOTE_REQUEST_LIFETIME,
            cancelled: true,
            delivery: RemoteDeliveryPlan::Herdr(
                crate::target_binding::synthetic_remote_session_identity(1),
            ),
        }));

        assert_eq!(
            active_remote_status(active.as_ref().unwrap()),
            RemoteStatus::Cancelling
        );
        assert!(!remote_slot_available(&stage, &active));
        let ActiveRemote::Processing(processing) = active.as_ref().unwrap() else {
            panic!("expected processing remote")
        };
        assert_eq!(
            processing_finish_status(processing, OperationOutcome::Succeeded),
            RemoteStatus::Cancelled
        );

        active = None;
        stage = Stage::Idle;
        assert!(remote_slot_available(&stage, &active));
    }

    #[test]
    fn ready_retains_exclusive_processing_ownership_until_one_commit_attempt() {
        let stage = Stage::Processing(OperationOwner::remote("request-a"));
        let identity = crate::target_binding::synthetic_remote_session_identity(1);
        let mut active = Some(ActiveRemote::Processing(ProcessingRemote {
            request_id: "request-a".into(),
            connection_id: 7,
            request_deadline: Instant::now() + REMOTE_REQUEST_LIFETIME,
            cancelled: false,
            delivery: RemoteDeliveryPlan::Herdr(identity.clone()),
        }));
        stage_ready_state(&stage, &mut active, "request-a", "staged text".into()).unwrap();
        assert_eq!(
            active_remote_status(active.as_ref().unwrap()),
            RemoteStatus::Ready
        );
        assert!(!remote_slot_available(&stage, &active));

        assert!(take_ready_for_commit(&mut active, 8, "request-a").is_err());
        assert!(matches!(active, Some(ActiveRemote::Ready(_))));
        let ready = take_ready_for_commit(&mut active, 7, "request-a").unwrap();
        assert_eq!(ready.text, "staged text");
        assert_eq!(ready.delivery, RemoteDeliveryPlan::Herdr(identity));
        assert!(active.is_none());
        assert!(take_ready_for_commit(&mut active, 7, "request-a").is_err());

        let calls = std::cell::Cell::new(0);
        assert_eq!(
            commit_delivery_status(|| {
                calls.set(calls.get() + 1);
                Ok(())
            }),
            RemoteStatus::Succeeded
        );
        assert_eq!(calls.get(), 1);
        assert_eq!(
            commit_delivery_status(|| Err("closed pane".to_string())),
            RemoteStatus::Failed
        );
    }

    #[test]
    fn local_ready_is_bounded_owned_and_consumed_at_most_once() {
        let stage = Stage::Processing(OperationOwner::remote("request-local"));
        let mut active = Some(ActiveRemote::Processing(ProcessingRemote {
            request_id: "request-local".into(),
            connection_id: 17,
            request_deadline: Instant::now() + REMOTE_REQUEST_LIFETIME,
            cancelled: false,
            delivery: RemoteDeliveryPlan::Local,
        }));
        assert!(stage_ready_state(
            &stage,
            &mut active,
            "request-local",
            "x".repeat(crate::clipboard::MAX_REMOTE_INJECTION_TEXT_BYTES),
        )
        .is_err());
        assert!(matches!(active, Some(ActiveRemote::Processing(_))));

        stage_ready_state(
            &stage,
            &mut active,
            "request-local",
            "bounded local text".into(),
        )
        .unwrap();
        assert!(take_ready_for_commit(&mut active, 18, "request-local").is_err());
        assert!(matches!(active, Some(ActiveRemote::Ready(_))));
        let ready = take_ready_for_commit(&mut active, 17, "request-local").unwrap();
        assert_eq!(ready.delivery, RemoteDeliveryPlan::Local);
        assert_eq!(ready.text, "bounded local text");
        assert!(take_ready_for_commit(&mut active, 17, "request-local").is_err());

        // Terminal cancellation or disconnect leaves no Ready value from which
        // any later connection/request could obtain an injection handoff.
        active = None;
        assert!(take_ready_for_commit(&mut active, 17, "request-local").is_err());
        assert!(take_ready_for_commit(&mut active, 18, "request-local").is_err());
    }

    #[test]
    fn early_commit_and_cancelled_or_blank_ready_are_refused_without_state_loss() {
        let stage = Stage::Processing(OperationOwner::remote("request-a"));
        let mut active = Some(ActiveRemote::Processing(ProcessingRemote {
            request_id: "request-a".into(),
            connection_id: 7,
            request_deadline: Instant::now() + REMOTE_REQUEST_LIFETIME,
            cancelled: false,
            delivery: RemoteDeliveryPlan::Local,
        }));
        assert!(take_ready_for_commit(&mut active, 7, "request-a").is_err());
        assert!(stage_ready_state(&stage, &mut active, "request-a", "  ".into()).is_err());
        assert!(matches!(active, Some(ActiveRemote::Processing(_))));

        let Some(ActiveRemote::Processing(processing)) = active.as_mut() else {
            panic!("processing state was not preserved")
        };
        processing.cancelled = true;
        assert!(stage_ready_state(&stage, &mut active, "request-a", "text".into()).is_err());
        assert!(matches!(active, Some(ActiveRemote::Processing(_))));
    }

    #[test]
    fn remote_audio_chunks_and_total_are_bounded_without_truncation() {
        assert!(next_remote_audio_total(0, 0).is_err());
        assert!(next_remote_audio_total(0, REMOTE_MAX_AUDIO_CHUNK_SAMPLES + 1).is_err());
        assert_eq!(
            next_remote_audio_total(10, REMOTE_MAX_AUDIO_CHUNK_SAMPLES).unwrap(),
            10 + REMOTE_MAX_AUDIO_CHUNK_SAMPLES
        );
        assert!(next_remote_audio_total(REMOTE_MAX_TOTAL_AUDIO_SAMPLES, 1).is_err());
    }

    #[test]
    fn terminal_status_is_bound_to_connection_and_expires() {
        let now = Instant::now();
        let mut terminals = VecDeque::new();
        push_terminal(
            &mut terminals,
            "request-a".into(),
            7,
            RemoteStatus::Succeeded,
            now,
        );
        assert_eq!(
            terminal_status(&mut terminals, 7, "request-a", now),
            Some(RemoteStatus::Succeeded)
        );
        assert_eq!(terminal_status(&mut terminals, 8, "request-a", now), None);
        assert_eq!(
            terminal_status(
                &mut terminals,
                7,
                "request-a",
                now + REMOTE_TERMINAL_LIFETIME + Duration::from_millis(1),
            ),
            None
        );
    }

    #[derive(Clone, Copy)]
    enum Event {
        Press,
        Release,
        Grace,
    }

    #[derive(Debug, PartialEq, Eq)]
    enum SimStage {
        Idle,
        Recording,
        Processing,
    }

    fn simulate(events: &[Event]) -> (u32, u32, SimStage) {
        let mut stage = SimStage::Idle;
        let mut pending = false;
        let mut last_press_ms: Option<u64> = None;
        let mut clock_ms = 0u64;
        let mut starts = 0;
        let mut stops = 0;

        for event in events {
            clock_ms += 5;
            match event {
                Event::Grace => {
                    if std::mem::take(&mut pending) && stage == SimStage::Recording {
                        stage = SimStage::Processing;
                        stops += 1;
                    }
                }
                Event::Press | Event::Release => {
                    let pressed = matches!(event, Event::Press);
                    match classify_ptt_event(
                        pending.then_some("transcribe"),
                        pressed,
                        true,
                        "transcribe",
                        (stage == SimStage::Recording).then_some("transcribe"),
                    ) {
                        PttAction::CancelRelease => {
                            pending = false;
                            continue;
                        }
                        PttAction::DeferRelease => {
                            pending = true;
                            continue;
                        }
                        PttAction::Passthrough => {}
                    }
                    if pressed {
                        if last_press_ms.is_some_and(|last| clock_ms - last < 30) {
                            continue;
                        }
                        last_press_ms = Some(clock_ms);
                    }
                    if pressed && stage == SimStage::Idle {
                        stage = SimStage::Recording;
                        starts += 1;
                    }
                }
            }
        }
        (starts, stops, stage)
    }

    fn autorepeat_burst() -> Vec<Event> {
        let mut events = vec![Event::Press];
        for _ in 0..6 {
            events.push(Event::Release);
            events.push(Event::Press);
        }
        events
    }

    #[test]
    fn x11_autorepeat_burst_does_not_toggle_recording() {
        let (starts, stops, stage) = simulate(&autorepeat_burst());
        assert_eq!((starts, stops, stage), (1, 0, SimStage::Recording));
    }

    #[test]
    fn genuine_release_after_grace_stops_once() {
        let mut events = autorepeat_burst();
        events.extend([Event::Release, Event::Grace]);
        let (starts, stops, stage) = simulate(&events);
        assert_eq!((starts, stops, stage), (1, 1, SimStage::Processing));
    }
}
