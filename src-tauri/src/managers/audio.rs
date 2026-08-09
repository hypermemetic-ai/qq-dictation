use crate::audio_toolkit::{
    audio::{accept_16khz_frame, AudioFrameCallback, FrameResampler, VadConfig},
    list_input_devices,
    vad::{
        SmoothedVad, VAD_OFFLINE_HANGOVER_FRAMES, VAD_ONSET_FRAMES, VAD_PREFILL_FRAMES,
        VAD_STREAMING_HANGOVER_FRAMES,
    },
    AudioRecorder, SileroVad, VadPolicy,
};
use crate::managers::transcription::StreamRouter;
use crate::operation::OperationOwner;
use crate::settings::{get_settings, AppSettings};
use crate::utils;
use log::{debug, error, info, warn};
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::Manager;

const STREAM_IDLE_TIMEOUT: Duration = Duration::from_secs(30);
const VAD_THRESHOLD: f32 = 0.3;

fn set_mute(mute: bool) {
    use std::process::Command;

    let mute_value = if mute { "1" } else { "0" };
    let amixer_state = if mute { "mute" } else { "unmute" };

    if Command::new("wpctl")
        .args(["set-mute", "@DEFAULT_AUDIO_SINK@", mute_value])
        .output()
        .map(|output| output.status.success())
        .unwrap_or(false)
    {
        return;
    }
    if Command::new("pactl")
        .args(["set-sink-mute", "@DEFAULT_SINK@", mute_value])
        .output()
        .map(|output| output.status.success())
        .unwrap_or(false)
    {
        return;
    }
    let _ = Command::new("amixer")
        .args(["set", "Master", amixer_state])
        .output();
}

/// Reads the current system output mute state, mirroring `set_mute`'s backends.
///
/// Returns `Some(true)`/`Some(false)` when the state could be determined, or
/// `None` when it couldn't (unsupported audio stack, missing CLI tools, or an
/// error). Callers treat `None` as "unknown" and fall back to unmuting on stop,
/// so we never strand the user's audio muted.
fn get_mute() -> Option<bool> {
    use std::process::Command;

    // 1. PipeWire (wpctl): prints "[MUTED]" in the volume line when muted.
    if let Ok(out) = Command::new("wpctl")
        .args(["get-volume", "@DEFAULT_AUDIO_SINK@"])
        .output()
    {
        if out.status.success() {
            return Some(String::from_utf8_lossy(&out.stdout).contains("[MUTED]"));
        }
    }

    // 2. PulseAudio (pactl): prints "Mute: yes" / "Mute: no".
    // Force LC_ALL=C so a localized system still emits the parseable English
    // "yes"/"no" instead of e.g. "ja"/"nein".
    if let Ok(out) = Command::new("pactl")
        .env("LC_ALL", "C")
        .args(["get-sink-mute", "@DEFAULT_SINK@"])
        .output()
    {
        if out.status.success() {
            let s = String::from_utf8_lossy(&out.stdout).to_lowercase();
            if s.contains("yes") {
                return Some(true);
            }
            if s.contains("no") {
                return Some(false);
            }
        }
    }

    // 3. ALSA (amixer): prints "[off]" for muted channels, "[on]" otherwise.
    // LC_ALL=C keeps the "[on]"/"[off]" tokens stable across locales.
    if let Ok(out) = Command::new("amixer")
        .env("LC_ALL", "C")
        .args(["get", "Master"])
        .output()
    {
        if out.status.success() {
            let s = String::from_utf8_lossy(&out.stdout);
            if s.contains("[off]") {
                return Some(true);
            }
            if s.contains("[on]") {
                return Some(false);
            }
        }
    }

    None
}

/// Restores the system mute state after our forced mute, given the state
/// captured just before we muted. We only ever need to unmute — and only when
/// the system was NOT already muted beforehand. If the prior state was muted,
/// we leave it muted (the user's own state). If it's unknown (`None`), we
/// default to unmuting so audio is never left stranded muted by us.
fn restore_mute(prev_muted: Option<bool>) {
    if prev_muted != Some(true) {
        set_mute(false);
    }
}

const WHISPER_SAMPLE_RATE: usize = 16000;

/* ──────────────────────────────────────────────────────────────── */

#[derive(Clone, Debug)]
pub(crate) enum RecordingState {
    Idle,
    Recording { owner: OperationOwner },
    Stopping { owner: OperationOwner },
}

#[derive(Clone, Debug)]
pub enum MicrophoneMode {
    AlwaysOn,
    OnDemand,
}

/// Tracks our forced "mute while recording" so we can restore the user's audio
/// exactly as it was. `did_mute` is true while our mute is active; `prev_muted`
/// is the system mute state captured just before we muted, used to decide
/// whether to unmute on stop (so a system that was already muted stays muted).
#[derive(Debug, Default, Clone, Copy)]
struct MuteState {
    did_mute: bool,
    prev_muted: Option<bool>,
}

/* ──────────────────────────────────────────────────────────────── */

fn create_vad_config(vad_path: &Path) -> Result<VadConfig, anyhow::Error> {
    // A single Silero engine covers both the offline and streaming policies (never
    // active at once within a recording), so the processor reconfigures its
    // hangover tail per session rather than keeping two ONNX sessions resident.
    let silero = SileroVad::new(vad_path, VAD_THRESHOLD)
        .map_err(|error| anyhow::anyhow!("Failed to create SileroVad: {error}"))?;
    let smoothed_vad = SmoothedVad::new(
        Box::new(silero),
        VAD_PREFILL_FRAMES,
        VAD_OFFLINE_HANGOVER_FRAMES,
        VAD_ONSET_FRAMES,
    );
    Ok(VadConfig::new(
        Box::new(smoothed_vad),
        VAD_OFFLINE_HANGOVER_FRAMES,
        VAD_STREAMING_HANGOVER_FRAMES,
    ))
}

fn create_audio_recorder(
    vad_path: &Path,
    app_handle: &tauri::AppHandle,
    stream_router: Arc<StreamRouter>,
) -> Result<AudioRecorder, anyhow::Error> {
    let vad = create_vad_config(vad_path)?;

    // Recorder with VAD, a spectrum-level callback that forwards level updates to
    // the frontend, and an audio-frame callback that feeds live streaming via a
    // shared `StreamRouter` (captured directly, not via Tauri state — see its docs).
    let recorder = AudioRecorder::new()
        .map_err(|e| anyhow::anyhow!("Failed to create AudioRecorder: {}", e))?
        .with_vad_config(vad)
        .with_level_callback({
            let app_handle = app_handle.clone();
            move |levels| {
                utils::emit_levels(&app_handle, &levels);
            }
        })
        .with_audio_callback({
            let router = stream_router;
            move |frame| {
                router.feed(frame);
            }
        });

    Ok(recorder)
}

struct RemoteAudioCapture {
    frames: FrameResampler,
    vad: Option<VadConfig>,
    vad_policy: VadPolicy,
    audio_callback: Option<AudioFrameCallback>,
    accepted_samples: Vec<f32>,
}

impl RemoteAudioCapture {
    fn new(vad: VadConfig, vad_policy: VadPolicy, stream_router: Arc<StreamRouter>) -> Self {
        let audio_callback: AudioFrameCallback = Arc::new(move |frame| stream_router.feed(frame));
        Self::with_callback(vad, vad_policy, audio_callback)
    }

    fn with_callback(
        vad: VadConfig,
        vad_policy: VadPolicy,
        audio_callback: AudioFrameCallback,
    ) -> Self {
        vad.prepare(vad_policy);
        Self {
            frames: FrameResampler::new(
                WHISPER_SAMPLE_RATE,
                WHISPER_SAMPLE_RATE,
                Duration::from_millis(30),
            ),
            vad: Some(vad),
            vad_policy,
            audio_callback: Some(audio_callback),
            accepted_samples: Vec::new(),
        }
    }

    fn push_pcm_s16le(&mut self, pcm: &[i16]) {
        let normalized = pcm
            .iter()
            .map(|sample| f32::from(*sample) / 32768.0)
            .collect::<Vec<_>>();
        let vad = &self.vad;
        let vad_policy = self.vad_policy;
        let audio_callback = &self.audio_callback;
        let accepted_samples = &mut self.accepted_samples;
        self.frames.push(&normalized, |frame| {
            accept_16khz_frame(
                frame,
                true,
                vad_policy,
                vad,
                audio_callback,
                accepted_samples,
            )
        });
    }

    fn finish(mut self) -> Vec<f32> {
        let vad = &self.vad;
        let vad_policy = self.vad_policy;
        let audio_callback = &self.audio_callback;
        let accepted_samples = &mut self.accepted_samples;
        self.frames.finish(|frame| {
            accept_16khz_frame(
                frame,
                true,
                vad_policy,
                vad,
                audio_callback,
                accepted_samples,
            )
        });
        self.accepted_samples
    }
}

/* ──────────────────────────────────────────────────────────────── */

#[derive(Clone)]
pub struct AudioRecordingManager {
    state: Arc<Mutex<RecordingState>>,
    mode: Arc<Mutex<MicrophoneMode>>,
    app_handle: tauri::AppHandle,

    recorder: Arc<Mutex<Option<AudioRecorder>>>,
    remote_capture: Arc<Mutex<Option<RemoteAudioCapture>>>,
    is_open: Arc<Mutex<bool>>,
    is_recording: Arc<Mutex<bool>>,
    mute_state: Arc<Mutex<MuteState>>,
    close_generation: Arc<AtomicU64>,
    cancel_generation: Arc<AtomicU64>,
    stream_router: Arc<StreamRouter>,
    /// Resolution of a named microphone to its cpal device, cached so
    /// on-demand recording starts skip the full device
    /// enumeration (~40-110ms). Keyed by the resolved name, so a settings
    /// change misses naturally; cleared when an open fails (device unplugged)
    /// so the retry re-enumerates. The system-default case is never cached —
    /// the recorder resolves the current default itself, cheaply.
    cached_device: Arc<Mutex<Option<(String, cpal::Device)>>>,
}

impl AudioRecordingManager {
    /* ---------- construction ------------------------------------------------ */

    pub fn new(
        app: &tauri::AppHandle,
        stream_router: Arc<StreamRouter>,
    ) -> Result<Self, anyhow::Error> {
        let settings = get_settings(app);
        let mode = if settings.always_on_microphone {
            MicrophoneMode::AlwaysOn
        } else {
            MicrophoneMode::OnDemand
        };

        let manager = Self {
            state: Arc::new(Mutex::new(RecordingState::Idle)),
            mode: Arc::new(Mutex::new(mode.clone())),
            app_handle: app.clone(),

            recorder: Arc::new(Mutex::new(None)),
            remote_capture: Arc::new(Mutex::new(None)),
            is_open: Arc::new(Mutex::new(false)),
            is_recording: Arc::new(Mutex::new(false)),
            mute_state: Arc::new(Mutex::new(MuteState::default())),
            close_generation: Arc::new(AtomicU64::new(0)),
            cancel_generation: Arc::new(AtomicU64::new(0)),
            stream_router,
            cached_device: Arc::new(Mutex::new(None)),
        };

        // Always-on?  Open immediately.
        if matches!(mode, MicrophoneMode::AlwaysOn) {
            manager.start_microphone_stream()?;
        }

        Ok(manager)
    }

    /* ---------- helper methods --------------------------------------------- */

    /// The selected microphone name, or `None` for the system default.
    fn desired_device_name(&self, settings: &AppSettings) -> Option<String> {
        settings.selected_microphone.clone()
    }

    pub fn invalidate_device_cache(&self) {
        *self.cached_device.lock().unwrap() = None;
    }

    fn get_effective_microphone_device(&self, settings: &AppSettings) -> Option<cpal::Device> {
        let device_name = match self.desired_device_name(settings) {
            Some(name) => name,
            None => {
                debug!("device resolve: no mic configured -> system default");
                return None;
            }
        };

        // Cache hit: skip the full enumeration. A stale device (unplugged)
        // fails at open, where the caller invalidates and retries fresh.
        if let Some((cached_name, device)) = self.cached_device.lock().unwrap().as_ref() {
            if *cached_name == device_name {
                debug!("device resolve: cache hit for '{}'", device_name);
                return Some(device.clone());
            }
        }

        // Find the device by name
        let enumerate_started = Instant::now();
        let device = match list_input_devices() {
            Ok(devices) => devices
                .into_iter()
                .find(|d| d.name == device_name)
                .map(|d| d.device),
            Err(e) => {
                debug!("Failed to list devices, using default: {}", e);
                None
            }
        };
        debug!(
            "device resolve: enumerate={:?} (found={})",
            enumerate_started.elapsed(),
            device.is_some()
        );
        if let Some(d) = &device {
            *self.cached_device.lock().unwrap() = Some((device_name, d.clone()));
        }
        device
    }

    fn schedule_lazy_close(&self) {
        let gen = self.close_generation.fetch_add(1, Ordering::SeqCst) + 1;
        let app = self.app_handle.clone();
        std::thread::spawn(move || {
            std::thread::sleep(STREAM_IDLE_TIMEOUT);
            let rm = app.state::<Arc<AudioRecordingManager>>();
            // Hold state lock across the check AND close to serialize against
            // try_start_recording, preventing a race where the stream is closed
            // under an active recording.
            let state = rm.state.lock().unwrap();
            if rm.close_generation.load(Ordering::SeqCst) == gen
                && matches!(*state, RecordingState::Idle)
            {
                // stop_microphone_stream does not acquire the state lock,
                // so holding it here is safe (no deadlock).
                info!(
                    "Closing idle microphone stream after {:?}",
                    STREAM_IDLE_TIMEOUT
                );
                rm.stop_microphone_stream();
            }
        });
    }

    /* ---------- microphone life-cycle -------------------------------------- */

    /// Applies mute if mute_while_recording is enabled and stream is open.
    /// Snapshots the system's prior mute state first so `remove_mute` can
    /// restore it instead of unconditionally unmuting.
    pub fn apply_mute(&self) {
        let settings = get_settings(&self.app_handle);
        if !settings.mute_while_recording {
            return;
        }

        // Lock order: is_open before mute_state (matches stop_microphone_stream).
        let is_open = self.is_open.lock().unwrap();
        let mut mute_guard = self.mute_state.lock().unwrap();
        // Already muted this session — don't re-snapshot, or a duplicate/late
        // apply would overwrite prev_muted with our own forced-muted state and
        // strand audio muted on stop.
        if mute_guard.did_mute {
            return;
        }
        if *is_open {
            mute_guard.prev_muted = get_mute();
            set_mute(true);
            mute_guard.did_mute = true;
            debug!("Mute applied (prev_muted={:?})", mute_guard.prev_muted);
        }
    }

    /// Removes mute if it was applied, restoring the system's prior mute state
    /// (a system already muted before recording stays muted).
    pub fn remove_mute(&self) {
        let mut mute_guard = self.mute_state.lock().unwrap();
        if mute_guard.did_mute {
            restore_mute(mute_guard.prev_muted);
            mute_guard.did_mute = false;
            debug!(
                "Mute removed (restored prev_muted={:?})",
                mute_guard.prev_muted
            );
        }
    }

    pub fn preload_vad(&self) -> Result<(), anyhow::Error> {
        let mut recorder_opt = self.recorder.lock().unwrap();
        if recorder_opt.is_none() {
            let vad_path = self
                .app_handle
                .path()
                .resolve(
                    "resources/models/silero_vad_v4.onnx",
                    tauri::path::BaseDirectory::Resource,
                )
                .map_err(|e| anyhow::anyhow!("Failed to resolve VAD path: {}", e))?;
            *recorder_opt = Some(create_audio_recorder(
                &vad_path,
                &self.app_handle,
                Arc::clone(&self.stream_router),
            )?);
        }
        Ok(())
    }

    pub fn start_microphone_stream(&self) -> Result<(), anyhow::Error> {
        let mut open_flag = self.is_open.lock().unwrap();
        if *open_flag {
            debug!("Microphone stream already active");
            return Ok(());
        }

        let start_time = Instant::now();

        // Don't mute immediately - caller will handle muting after audio feedback.
        // The previous stream restored audio on close, so did_mute should already
        // be false here; if it somehow isn't, restore rather than just clearing the
        // flag, which would strand system audio muted.
        {
            let mut mute_guard = self.mute_state.lock().unwrap();
            if mute_guard.did_mute {
                restore_mute(mute_guard.prev_muted);
                mute_guard.did_mute = false;
            }
        }

        // Get the selected device from settings.
        // No pre-flight enumeration here: when nothing is configured the
        // recorder resolves the system default itself, and a machine with no
        // input devices at all fails inside open() with the same
        // "No input device found" error this used to check for.
        let settings = get_settings(&self.app_handle);
        let resolve_started = Instant::now();
        let selected_device = self.get_effective_microphone_device(&settings);
        let resolve_elapsed = resolve_started.elapsed();

        // Ensure VAD is loaded if it wasn't for whatever reason
        let vad_started = Instant::now();
        self.preload_vad()?;
        let vad_elapsed = vad_started.elapsed();

        let open_started = Instant::now();
        let mut recorder_opt = self.recorder.lock().unwrap();
        if let Some(rec) = recorder_opt.as_mut() {
            if let Err(first_err) = rec.open(selected_device.clone()) {
                // A cached device or config may have gone stale (unplugged,
                // rate/format changed). Re-resolve from a fresh enumeration and
                // retry once before surfacing the error.
                warn!("Recorder open failed ({first_err}); re-resolving device and retrying once");
                self.invalidate_device_cache();
                let fresh_device = self.get_effective_microphone_device(&settings);
                rec.open(fresh_device)
                    .map_err(|e| anyhow::anyhow!("Failed to open recorder: {}", e))?;
            }
        }
        debug!(
            "mic stream breakdown: device_resolve={:?} vad_ensure={:?} open={:?}",
            resolve_elapsed,
            vad_elapsed,
            open_started.elapsed()
        );

        *open_flag = true;
        // This timing covers through cpal's stream.play() returning — i.e. the
        // point cpal surfaces as "stream running." It does NOT guarantee the
        // host audio device is producing samples yet; the first input callback
        // fires asynchronously one buffer period later (hardware dependent,
        // typically ~10–200ms, longer on Bluetooth/USB).
        info!(
            "Microphone stream initialized in {:?}",
            start_time.elapsed()
        );
        Ok(())
    }

    pub fn stop_microphone_stream(&self) {
        let mut open_flag = self.is_open.lock().unwrap();
        if !*open_flag {
            return;
        }

        {
            let mut mute_guard = self.mute_state.lock().unwrap();
            if mute_guard.did_mute {
                restore_mute(mute_guard.prev_muted);
            }
            mute_guard.did_mute = false;
        }

        if let Some(rec) = self.recorder.lock().unwrap().as_mut() {
            // If still recording, stop first.
            if *self.is_recording.lock().unwrap() {
                let _ = rec.stop();
                *self.is_recording.lock().unwrap() = false;
            }
            let _ = rec.close();
        }

        *open_flag = false;
        debug!("Microphone stream stopped");
    }

    /* ---------- mode switching --------------------------------------------- */

    pub fn update_mode(&self, new_mode: MicrophoneMode) -> Result<(), anyhow::Error> {
        let cur_mode = self.mode.lock().unwrap().clone();

        match (cur_mode, &new_mode) {
            (MicrophoneMode::AlwaysOn, MicrophoneMode::OnDemand) => {
                if matches!(*self.state.lock().unwrap(), RecordingState::Idle) {
                    self.close_generation.fetch_add(1, Ordering::SeqCst);
                    self.stop_microphone_stream();
                }
            }
            (MicrophoneMode::OnDemand, MicrophoneMode::AlwaysOn) => {
                self.close_generation.fetch_add(1, Ordering::SeqCst);
                self.start_microphone_stream()?;
            }
            _ => {}
        }

        *self.mode.lock().unwrap() = new_mode;
        Ok(())
    }

    /* ---------- recording --------------------------------------------------- */

    pub fn try_start_recording(
        &self,
        binding_id: &str,
        vad_policy: VadPolicy,
    ) -> Result<(), String> {
        let mut state = self.state.lock().unwrap();

        if let RecordingState::Idle = *state {
            // Ensure microphone is open in on-demand mode
            if matches!(*self.mode.lock().unwrap(), MicrophoneMode::OnDemand) {
                // Cancel any pending lazy close
                self.close_generation.fetch_add(1, Ordering::SeqCst);
                if let Err(e) = self.start_microphone_stream() {
                    let msg = format!("{e}");
                    error!("Failed to open microphone stream: {msg}");
                    return Err(msg);
                }
            }

            if let Some(rec) = self.recorder.lock().unwrap().as_ref() {
                if rec.start(vad_policy).is_ok() {
                    let owner = OperationOwner::local(binding_id);
                    *self.is_recording.lock().unwrap() = true;
                    *state = RecordingState::Recording {
                        owner: owner.clone(),
                    };
                    debug!("Recording started for {owner}");
                    return Ok(());
                }
            }
            Err("Recorder not available".to_string())
        } else {
            Err("Already recording".to_string())
        }
    }

    pub(crate) fn try_start_remote(
        &self,
        request_id: &str,
        vad_policy: VadPolicy,
    ) -> Result<(), String> {
        let vad_path = self
            .app_handle
            .path()
            .resolve(
                "resources/models/silero_vad_v4.onnx",
                tauri::path::BaseDirectory::Resource,
            )
            .map_err(|error| format!("Failed to resolve VAD path: {error}"))?;
        let vad = create_vad_config(&vad_path).map_err(|error| error.to_string())?;
        let capture = RemoteAudioCapture::new(vad, vad_policy, Arc::clone(&self.stream_router));

        let mut state = self.state.lock().unwrap();
        if !matches!(*state, RecordingState::Idle) {
            return Err("Another recording owns the audio pipeline".to_string());
        }
        let owner = OperationOwner::remote(request_id);
        *self.remote_capture.lock().unwrap() = Some(capture);
        *self.is_recording.lock().unwrap() = true;
        *state = RecordingState::Recording {
            owner: owner.clone(),
        };
        debug!("Remote recording started for {owner}");
        Ok(())
    }

    pub(crate) fn feed_remote_pcm(&self, request_id: &str, pcm: &[i16]) -> Result<(), String> {
        let owner = OperationOwner::remote(request_id);
        let state = self.state.lock().unwrap();
        if !matches!(&*state, RecordingState::Recording { owner: active } if active == &owner) {
            return Err("Remote request does not own the recording pipeline".to_string());
        }
        self.remote_capture
            .lock()
            .unwrap()
            .as_mut()
            .ok_or_else(|| "Remote capture pipeline is unavailable".to_string())?
            .push_pcm_s16le(pcm);
        Ok(())
    }

    pub fn update_selected_device(&self) -> Result<(), anyhow::Error> {
        // Device settings changed; drop the cached resolution so the next
        // open re-enumerates. (The name-keyed cache would miss anyway; this
        // just avoids holding a stale cpal::Device alive.)
        self.invalidate_device_cache();
        // If currently open, restart the microphone stream to use the new device
        if *self.is_open.lock().unwrap() {
            self.close_generation.fetch_add(1, Ordering::SeqCst);
            self.stop_microphone_stream();
            self.start_microphone_stream()?;
        }
        Ok(())
    }

    pub fn cancel_generation(&self) -> u64 {
        self.cancel_generation.load(Ordering::Acquire)
    }

    pub fn was_cancelled_since(&self, generation: u64) -> bool {
        self.cancel_generation.load(Ordering::Acquire) != generation
    }

    pub(crate) fn stop_owned(
        &self,
        owner: &OperationOwner,
        cancel_generation: u64,
    ) -> Option<Vec<f32>> {
        let mut state = self.state.lock().unwrap();

        match &*state {
            RecordingState::Recording { owner: active } if active == owner => {
                *state = RecordingState::Stopping {
                    owner: owner.clone(),
                };
                drop(state);

                // The local microphone may keep capturing for the configured
                // trailing buffer. A remote sender has already stopped its
                // microphone before Finish, so delaying here cannot add audio.
                if owner.is_local() {
                    let buffer_ms = get_settings(&self.app_handle).extra_recording_buffer_ms;
                    if buffer_ms > 0 {
                        debug!("Extra recording buffer: sleeping {buffer_ms}ms before stopping");
                        let started = Instant::now();
                        let buffer = Duration::from_millis(buffer_ms);
                        while started.elapsed() < buffer {
                            if self.was_cancelled_since(cancel_generation) {
                                debug!("Recording stop cancelled during extra buffer");
                                break;
                            }
                            let remaining = buffer.saturating_sub(started.elapsed());
                            std::thread::sleep(remaining.min(Duration::from_millis(25)));
                        }
                    }
                }

                let samples = if owner.is_local() {
                    if let Some(recorder) = self.recorder.lock().unwrap().as_ref() {
                        match recorder.stop() {
                            Ok(samples) => samples,
                            Err(error) => {
                                error!("stop() failed: {error}");
                                Vec::new()
                            }
                        }
                    } else {
                        error!("Recorder not available");
                        Vec::new()
                    }
                } else {
                    self.remote_capture
                        .lock()
                        .unwrap()
                        .take()
                        .map(RemoteAudioCapture::finish)
                        .unwrap_or_default()
                };

                *self.is_recording.lock().unwrap() = false;
                *self.state.lock().unwrap() = RecordingState::Idle;

                if owner.is_local()
                    && matches!(*self.mode.lock().unwrap(), MicrophoneMode::OnDemand)
                {
                    if get_settings(&self.app_handle).lazy_stream_close {
                        self.schedule_lazy_close();
                    } else {
                        self.stop_microphone_stream();
                    }
                }

                if self.was_cancelled_since(cancel_generation) {
                    debug!("Recording stop cancelled; discarding captured samples");
                    return None;
                }

                // Preserve the existing short-recording padding for both sources.
                if samples.len() < WHISPER_SAMPLE_RATE && !samples.is_empty() {
                    let mut padded = samples;
                    padded.resize(WHISPER_SAMPLE_RATE * 5 / 4, 0.0);
                    Some(padded)
                } else {
                    Some(samples)
                }
            }
            _ => None,
        }
    }

    pub fn is_recording(&self) -> bool {
        matches!(
            *self.state.lock().unwrap(),
            RecordingState::Recording { .. } | RecordingState::Stopping { .. }
        )
    }

    pub(crate) fn active_owner(&self) -> Option<OperationOwner> {
        match &*self.state.lock().unwrap() {
            RecordingState::Recording { owner } | RecordingState::Stopping { owner } => {
                Some(owner.clone())
            }
            RecordingState::Idle => None,
        }
    }

    pub(crate) fn cancel_processing(&self) {
        self.cancel_generation.fetch_add(1, Ordering::AcqRel);
    }

    /// Cancel only when the caller names the source that currently owns capture.
    pub(crate) fn cancel_owned(&self, owner: &OperationOwner) -> bool {
        let mut state = self.state.lock().unwrap();
        let active_matches = matches!(
            &*state,
            RecordingState::Recording { owner: active }
                | RecordingState::Stopping { owner: active }
                if active == owner
        );
        if !active_matches {
            return false;
        }
        self.cancel_generation.fetch_add(1, Ordering::AcqRel);

        match &*state {
            RecordingState::Recording { .. } => {
                *state = RecordingState::Idle;
                drop(state);

                if owner.is_local() {
                    if let Some(recorder) = self.recorder.lock().unwrap().as_ref() {
                        let _ = recorder.stop();
                    }
                    if matches!(*self.mode.lock().unwrap(), MicrophoneMode::OnDemand) {
                        if get_settings(&self.app_handle).lazy_stream_close {
                            self.schedule_lazy_close();
                        } else {
                            self.stop_microphone_stream();
                        }
                    }
                } else {
                    self.remote_capture.lock().unwrap().take();
                }
                *self.is_recording.lock().unwrap() = false;
            }
            RecordingState::Stopping { .. } => {
                debug!("Cancellation requested while recording is stopping");
            }
            RecordingState::Idle => unreachable!("active owner was checked above"),
        }
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::audio_toolkit::vad::{VadFrame, VoiceActivityDetector};

    struct SpeechVad;

    impl VoiceActivityDetector for SpeechVad {
        fn push_frame<'a>(&'a mut self, frame: &'a [f32]) -> anyhow::Result<VadFrame<'a>> {
            Ok(VadFrame::Speech(frame))
        }
    }

    #[test]
    fn remote_pcm_uses_shared_vad_buffer_and_stream_callback_seam() {
        let vad = VadConfig::new(Box::new(SpeechVad), 15, 55);
        let streamed = Arc::new(Mutex::new(Vec::<f32>::new()));
        let streamed_for_callback = Arc::clone(&streamed);
        let callback: AudioFrameCallback = Arc::new(move |frame| {
            streamed_for_callback
                .lock()
                .unwrap()
                .extend_from_slice(frame)
        });
        let mut capture = RemoteAudioCapture::with_callback(vad, VadPolicy::Streaming, callback);

        let pcm = vec![16_384i16; 480];
        capture.push_pcm_s16le(&pcm);
        let accepted = capture.finish();
        let streamed = streamed.lock().unwrap().clone();

        assert_eq!(accepted.len(), 480);
        assert_eq!(streamed, accepted);
        assert!(accepted
            .iter()
            .all(|sample| (*sample - 0.5).abs() < f32::EPSILON));
    }
}
