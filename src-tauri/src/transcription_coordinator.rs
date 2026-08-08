use crate::actions::{
    finish_remote_operation, start_remote_operation, RemoteOperationPlan, ACTION_MAP,
};
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
pub(crate) const REMOTE_PENDING_CLAIM_LIFETIME: Duration = Duration::from_secs(5);
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
    Pending,
    Bound { pane_id: String },
    Processing,
    Succeeded,
    Failed,
    Cancelled,
}

struct PendingRemote {
    request_id: String,
    connection_id: u64,
    claim_deadline: Instant,
    request_deadline: Instant,
}

struct BoundRemote {
    request_id: String,
    connection_id: u64,
    pane_id: String,
    request_deadline: Instant,
    total_audio_samples: usize,
    plan: RemoteOperationPlan,
}

struct ProcessingRemote {
    request_id: String,
    connection_id: u64,
    request_deadline: Instant,
    target_token: u64,
    cancelled: bool,
}

enum ActiveRemote {
    Pending(PendingRemote),
    Bound(BoundRemote),
    Processing(ProcessingRemote),
}

impl ActiveRemote {
    fn request_id(&self) -> &str {
        match self {
            Self::Pending(remote) => &remote.request_id,
            Self::Bound(remote) => &remote.request_id,
            Self::Processing(remote) => &remote.request_id,
        }
    }

    fn connection_id(&self) -> u64 {
        match self {
            Self::Pending(remote) => remote.connection_id,
            Self::Bound(remote) => remote.connection_id,
            Self::Processing(remote) => remote.connection_id,
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
        reply: Sender<Result<(), String>>,
    },
    RemoteBind {
        pane_id: String,
        reply: Sender<Result<String, String>>,
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
    RemoteCancel {
        connection_id: u64,
        request_id: String,
        reply: Sender<Result<(), String>>,
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
    RemotePending(String),
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
        Stage::RemotePending(_) | Stage::Recording(_) | Stage::Processing(_) => {
            ModeSpaceAction::Ignore
        }
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

fn expire_remote(
    app: &AppHandle,
    stage: &mut Stage,
    active_remote: &mut Option<ActiveRemote>,
    now: Instant,
) {
    let expired = match active_remote.as_ref() {
        Some(ActiveRemote::Pending(remote)) => {
            now >= remote.claim_deadline || now >= remote.request_deadline
        }
        Some(ActiveRemote::Bound(remote)) => now >= remote.request_deadline,
        Some(ActiveRemote::Processing(remote)) => {
            now >= remote.request_deadline && !remote.cancelled
        }
        None => false,
    };
    if !expired {
        return;
    }

    match active_remote.as_mut() {
        Some(ActiveRemote::Pending(remote)) => {
            warn!("Remote pending request {} expired", remote.request_id);
            *active_remote = None;
            *stage = Stage::Idle;
        }
        Some(ActiveRemote::Bound(remote)) => {
            warn!("Remote recording request {} expired", remote.request_id);
            let owner = OperationOwner::remote(&remote.request_id);
            crate::utils::cancel_owned_operation(app, &owner, true);
            crate::target_binding::discard(remote.plan.target_token);
            *active_remote = None;
            *stage = Stage::Idle;
        }
        Some(ActiveRemote::Processing(remote)) => {
            warn!("Remote processing request {} expired", remote.request_id);
            let owner = OperationOwner::remote(&remote.request_id);
            crate::utils::cancel_owned_operation(app, &owner, false);
            crate::target_binding::discard(remote.target_token);
            remote.cancelled = true;
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
                                if let Some(ActiveRemote::Processing(remote)) = active_remote.take()
                                {
                                    if owner.remote_request_id() == Some(&remote.request_id) {
                                        crate::target_binding::discard(remote.target_token);
                                        push_terminal(
                                            &mut terminals,
                                            remote.request_id,
                                            remote.connection_id,
                                            if remote.cancelled {
                                                RemoteStatus::Cancelled
                                            } else {
                                                finish_status(outcome)
                                            },
                                            Instant::now(),
                                        );
                                    } else {
                                        active_remote = Some(ActiveRemote::Processing(remote));
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
                            reply,
                        } => {
                            let now = Instant::now();
                            expire_remote(&app, &mut stage, &mut active_remote, now);
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
                                active_remote = Some(ActiveRemote::Pending(PendingRemote {
                                    request_id: request_id.clone(),
                                    connection_id,
                                    claim_deadline: now + REMOTE_PENDING_CLAIM_LIFETIME,
                                    request_deadline: now + REMOTE_REQUEST_LIFETIME,
                                }));
                                stage = Stage::RemotePending(request_id);
                                Ok(())
                            };
                            let _ = reply.send(result);
                        }
                        Command::RemoteBind { pane_id, reply } => {
                            let now = Instant::now();
                            expire_remote(&app, &mut stage, &mut active_remote, now);
                            let result = match active_remote.take() {
                                Some(ActiveRemote::Pending(pending)) if matches!(&stage, Stage::RemotePending(id) if id == &pending.request_id) => {
                                    match crate::target_binding::pane_is_live(&pane_id).and_then(
                                        |()| {
                                            start_remote_operation(
                                                &app,
                                                &pending.request_id,
                                                &pane_id,
                                            )
                                        },
                                    ) {
                                        Ok(plan) => {
                                            let request_id = pending.request_id.clone();
                                            stage = Stage::Recording(OperationOwner::remote(
                                                &request_id,
                                            ));
                                            active_remote =
                                                Some(ActiveRemote::Bound(BoundRemote {
                                                    request_id: request_id.clone(),
                                                    connection_id: pending.connection_id,
                                                    pane_id,
                                                    request_deadline: pending.request_deadline,
                                                    total_audio_samples: 0,
                                                    plan,
                                                }));
                                            Ok(request_id)
                                        }
                                        Err(error) => {
                                            stage = Stage::Idle;
                                            Err(error)
                                        }
                                    }
                                }
                                Some(other) => {
                                    active_remote = Some(other);
                                    Err("There is no sole pending target claim".to_string())
                                }
                                None => Err("There is no pending target claim".to_string()),
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
                            expire_remote(&app, &mut stage, &mut active_remote, now);
                            let result =
                                match active_remote.as_mut() {
                                    Some(ActiveRemote::Bound(remote))
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
                            expire_remote(&app, &mut stage, &mut active_remote, now);
                            let result = match active_remote.take() {
                                Some(ActiveRemote::Bound(remote))
                                    if remote.connection_id == connection_id
                                        && remote.request_id == request_id =>
                                {
                                    if remote.total_audio_samples == 0 {
                                        active_remote = Some(ActiveRemote::Bound(remote));
                                        Err("Cannot finish before audio is accepted".to_string())
                                    } else {
                                        let owner = OperationOwner::remote(&request_id);
                                        stage = Stage::Processing(owner);
                                        let processing = ProcessingRemote {
                                            request_id: request_id.clone(),
                                            connection_id,
                                            request_deadline: remote.request_deadline,
                                            target_token: remote.plan.target_token,
                                            cancelled: false,
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
                            expire_remote(&app, &mut stage, &mut active_remote, now);
                            let result = match active_remote.as_ref() {
                                Some(remote)
                                    if remote_matches(remote, connection_id, &request_id) =>
                                {
                                    Ok(match remote {
                                        ActiveRemote::Pending(_) => RemoteStatus::Pending,
                                        ActiveRemote::Bound(remote) => RemoteStatus::Bound {
                                            pane_id: remote.pane_id.clone(),
                                        },
                                        ActiveRemote::Processing(remote) if remote.cancelled => {
                                            RemoteStatus::Cancelled
                                        }
                                        ActiveRemote::Processing(_) => RemoteStatus::Processing,
                                    })
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
                            expire_remote(&app, &mut stage, &mut active_remote, Instant::now());
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
    ) -> Result<(), String> {
        self.request(|reply| Command::RemoteStart {
            connection_id,
            request_id,
            reply,
        })
    }

    pub(crate) fn remote_bind(&self, pane_id: String) -> Result<String, String> {
        self.request(|reply| Command::RemoteBind { pane_id, reply })
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

    pub(crate) fn remote_cancel(
        &self,
        connection_id: u64,
        request_id: String,
    ) -> Result<(), String> {
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
) -> Result<(), String> {
    let Some(remote) = active_remote.as_mut() else {
        return Err("No remote request is active".to_string());
    };
    if !remote_matches(remote, connection_id, request_id) {
        return Err("Remote cancel request is not owner".to_string());
    }

    match remote {
        ActiveRemote::Pending(_) => {
            *active_remote = None;
            *stage = Stage::Idle;
        }
        ActiveRemote::Bound(remote) => {
            let owner = OperationOwner::remote(&remote.request_id);
            crate::utils::cancel_owned_operation(app, &owner, true);
            crate::target_binding::discard(remote.plan.target_token);
            *active_remote = None;
            *stage = Stage::Idle;
        }
        ActiveRemote::Processing(remote) => {
            let owner = OperationOwner::remote(&remote.request_id);
            crate::utils::cancel_owned_operation(app, &owner, false);
            crate::target_binding::discard(remote.target_token);
            remote.cancelled = true;
        }
    }
    Ok(())
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
        assert_eq!(
            classify_mode_space(true, &Stage::RemotePending("request-a".into())),
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
    fn remote_pending_claim_is_a_single_global_slot() {
        let mut stage = Stage::Idle;
        let mut active = None;
        assert!(remote_slot_available(&stage, &active));

        stage = Stage::RemotePending("request-a".into());
        active = Some(ActiveRemote::Pending(PendingRemote {
            request_id: "request-a".into(),
            connection_id: 7,
            claim_deadline: Instant::now() + REMOTE_PENDING_CLAIM_LIFETIME,
            request_deadline: Instant::now() + REMOTE_REQUEST_LIFETIME,
        }));
        assert!(!remote_slot_available(&stage, &active));
        assert_eq!(active.as_ref().unwrap().request_id(), "request-a");
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
