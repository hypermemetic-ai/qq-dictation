//! Same-user workstation ingress for remote Linux/X11 dictation.
//!
//! The SSH helper is only a byte bridge. This module owns protocol validation,
//! request/connection identity, the owner-only Unix socket, and dispatch into
//! the app's single [`TranscriptionCoordinator`] authority.

use crate::actions::RemoteDeliveryMode;
use crate::clipboard::RemoteInjectionPlan;
use crate::transcription_coordinator::{
    RemoteCancelStatus, RemoteCommitOutcome, RemoteStatus, TranscriptionCoordinator,
};
use log::{debug, error, info, warn};
use serde::{Deserialize, Serialize};
use std::fs;
use std::io::{self, Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::fs::{FileTypeExt, MetadataExt, PermissionsExt};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;
use tauri::{AppHandle, Manager};

pub(crate) const PROTOCOL_VERSION: u8 = 1;
pub(crate) const MAX_PROTOCOL_FRAME_BYTES: usize = 65_536;
pub(crate) const REMOTE_AUDIO_FORMAT: &str = "s16le";
pub(crate) const REMOTE_AUDIO_SAMPLE_RATE: u32 = 16_000;
pub(crate) const REMOTE_AUDIO_CHANNELS: u8 = 1;
const SOCKET_DIRECTORY: &str = "qq-dictation";
const SOCKET_FILE: &str = "remote.sock";
const MAX_CONNECTIONS: usize = 8;
const ACCEPT_POLL: Duration = Duration::from_millis(100);
const CONNECTION_IDLE_TIMEOUT: Duration = Duration::from_secs(30);

static NEXT_REQUEST_ID: AtomicU64 = AtomicU64::new(1);
static NEXT_CONNECTION_ID: AtomicU64 = AtomicU64::new(1);

#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct AudioFormat {
    format: String,
    sample_rate: u32,
    channels: u8,
}

impl AudioFormat {
    fn validate(&self) -> Result<(), String> {
        if self.format != REMOTE_AUDIO_FORMAT
            || self.sample_rate != REMOTE_AUDIO_SAMPLE_RATE
            || self.channels != REMOTE_AUDIO_CHANNELS
        {
            return Err(format!(
                "expected {REMOTE_AUDIO_FORMAT}/{REMOTE_AUDIO_SAMPLE_RATE}Hz/{REMOTE_AUDIO_CHANNELS}ch"
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Deserialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
enum WireRequest {
    Start {
        version: u8,
        #[serde(default)]
        delivery_mode: RemoteDeliveryMode,
        audio: AudioFormat,
    },
    Audio {
        version: u8,
        request_id: String,
        pcm: Vec<i16>,
    },
    Finish {
        version: u8,
        request_id: String,
    },
    Cancel {
        version: u8,
        request_id: String,
    },
    Commit {
        version: u8,
        request_id: String,
    },
    Status {
        version: u8,
        request_id: String,
    },
}

impl WireRequest {
    fn version(&self) -> u8 {
        match self {
            Self::Start { version, .. }
            | Self::Audio { version, .. }
            | Self::Finish { version, .. }
            | Self::Cancel { version, .. }
            | Self::Commit { version, .. }
            | Self::Status { version, .. } => *version,
        }
    }
}

#[derive(Debug, Serialize)]
struct WireResponse {
    version: u8,
    status: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    request_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    injection: Option<RemoteInjectionPlan>,
}

impl WireResponse {
    fn status(status: &str, request_id: Option<&str>) -> Self {
        Self {
            version: PROTOCOL_VERSION,
            status: status.to_string(),
            request_id: request_id.map(str::to_string),
            error: None,
            injection: None,
        }
    }

    fn error(error: &str) -> Self {
        Self {
            version: PROTOCOL_VERSION,
            status: "error".to_string(),
            request_id: None,
            error: Some(error.to_string()),
            injection: None,
        }
    }
}

#[derive(Default)]
struct ConnectionSession {
    request_id: Option<String>,
}

impl ConnectionSession {
    fn begin(&mut self, request_id: String) -> Result<(), String> {
        if self.request_id.is_some() {
            return Err("A helper connection may own only one active request".to_string());
        }
        self.request_id = Some(request_id);
        Ok(())
    }

    fn authorize(&self, request_id: &str) -> Result<(), String> {
        if self.request_id.as_deref() == Some(request_id) {
            Ok(())
        } else {
            Err("Request id is stale, replayed, or not owned by this connection".to_string())
        }
    }

    fn retire(&mut self, request_id: &str) -> Result<(), String> {
        self.authorize(request_id)?;
        self.request_id = None;
        Ok(())
    }

    fn is_idle(&self) -> bool {
        self.request_id.is_none()
    }
}

#[derive(Clone, Copy)]
struct SocketIdentity {
    device: u64,
    inode: u64,
}

impl SocketIdentity {
    fn from_metadata(metadata: &fs::Metadata) -> Self {
        Self {
            device: metadata.dev(),
            inode: metadata.ino(),
        }
    }

    fn matches(self, metadata: &fs::Metadata) -> bool {
        metadata.dev() == self.device && metadata.ino() == self.inode
    }
}

pub(crate) struct RemoteIngress {
    socket_path: PathBuf,
    socket_identity: SocketIdentity,
    shutdown: Arc<AtomicBool>,
    thread: Mutex<Option<thread::JoinHandle<()>>>,
}

impl RemoteIngress {
    pub(crate) fn start(app: AppHandle) -> Result<Arc<Self>, String> {
        let runtime_dir = std::env::var_os("XDG_RUNTIME_DIR")
            .map(PathBuf::from)
            .ok_or_else(|| {
                "XDG_RUNTIME_DIR is required for remote dictation ingress".to_string()
            })?;
        let socket_path = prepare_socket_path(&runtime_dir, unsafe { libc::geteuid() })?;
        let listener = UnixListener::bind(&socket_path)
            .map_err(|error| format!("Failed to bind {}: {error}", socket_path.display()))?;
        fs::set_permissions(&socket_path, fs::Permissions::from_mode(0o600))
            .map_err(|error| format!("Failed to secure {}: {error}", socket_path.display()))?;
        let metadata = fs::symlink_metadata(&socket_path)
            .map_err(|error| format!("Failed to inspect {}: {error}", socket_path.display()))?;
        if !metadata.file_type().is_socket()
            || metadata.uid() != unsafe { libc::geteuid() }
            || metadata.permissions().mode() & 0o077 != 0
        {
            let _ = fs::remove_file(&socket_path);
            return Err("Remote ingress socket did not satisfy owner-only policy".to_string());
        }
        let socket_identity = SocketIdentity::from_metadata(&metadata);
        listener
            .set_nonblocking(true)
            .map_err(|error| format!("Failed to configure remote ingress listener: {error}"))?;

        let shutdown = Arc::new(AtomicBool::new(false));
        let shutdown_for_thread = Arc::clone(&shutdown);
        let app_for_thread = app.clone();
        let active_connections = Arc::new(AtomicUsize::new(0));
        let active_for_thread = Arc::clone(&active_connections);
        let thread = thread::spawn(move || {
            info!("Remote dictation ingress listening on owner-only Unix socket");
            while !shutdown_for_thread.load(Ordering::Acquire) {
                if let Some(coordinator) = app_for_thread.try_state::<TranscriptionCoordinator>() {
                    coordinator.remote_tick();
                }
                match listener.accept() {
                    Ok((stream, _address)) => {
                        let accepted = active_for_thread
                            .fetch_update(Ordering::AcqRel, Ordering::Acquire, |count| {
                                (count < MAX_CONNECTIONS).then_some(count + 1)
                            })
                            .is_ok();
                        if !accepted {
                            warn!("Refusing remote ingress connection limit overflow");
                            continue;
                        }
                        let app = app_for_thread.clone();
                        let active = Arc::clone(&active_for_thread);
                        thread::spawn(move || {
                            struct CountGuard(Arc<AtomicUsize>);
                            impl Drop for CountGuard {
                                fn drop(&mut self) {
                                    self.0.fetch_sub(1, Ordering::AcqRel);
                                }
                            }
                            let _count_guard = CountGuard(active);
                            if let Err(error) = handle_connection(app, stream) {
                                debug!("Remote ingress connection closed: {error}");
                            }
                        });
                    }
                    Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                        thread::sleep(ACCEPT_POLL);
                    }
                    Err(error) => {
                        error!("Remote ingress accept failed: {error}");
                        thread::sleep(ACCEPT_POLL);
                    }
                }
            }
        });

        Ok(Arc::new(Self {
            socket_path,
            socket_identity,
            shutdown,
            thread: Mutex::new(Some(thread)),
        }))
    }

    pub(crate) fn shutdown(&self) {
        self.shutdown.store(true, Ordering::Release);
        if let Some(thread) = self.thread.lock().unwrap().take() {
            let _ = thread.join();
        }
        remove_if_same_socket(&self.socket_path, self.socket_identity);
        if let Some(parent) = self.socket_path.parent() {
            let _ = fs::remove_dir(parent);
        }
    }
}

impl Drop for RemoteIngress {
    fn drop(&mut self) {
        self.shutdown();
    }
}

fn prepare_socket_path(runtime_dir: &Path, expected_uid: u32) -> Result<PathBuf, String> {
    let runtime_metadata = fs::symlink_metadata(runtime_dir)
        .map_err(|error| format!("Cannot inspect XDG_RUNTIME_DIR: {error}"))?;
    if !runtime_metadata.file_type().is_dir() || runtime_metadata.uid() != expected_uid {
        return Err("XDG_RUNTIME_DIR must be a directory owned by the current user".to_string());
    }

    let directory = runtime_dir.join(SOCKET_DIRECTORY);
    match fs::symlink_metadata(&directory) {
        Ok(metadata) => {
            if !metadata.file_type().is_dir()
                || metadata.uid() != expected_uid
                || metadata.permissions().mode() & 0o077 != 0
            {
                return Err("Remote ingress directory is not an owner-only directory".to_string());
            }
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            fs::create_dir(&directory).map_err(|error| {
                format!(
                    "Failed to create remote ingress directory {}: {error}",
                    directory.display()
                )
            })?;
            fs::set_permissions(&directory, fs::Permissions::from_mode(0o700))
                .map_err(|error| format!("Failed to secure {}: {error}", directory.display()))?;
        }
        Err(error) => return Err(format!("Cannot inspect {}: {error}", directory.display())),
    }

    let socket_path = directory.join(SOCKET_FILE);
    let existing = match fs::symlink_metadata(&socket_path) {
        Ok(metadata) => Some(metadata),
        Err(error) if error.kind() == io::ErrorKind::NotFound => None,
        Err(error) => return Err(format!("Cannot inspect {}: {error}", socket_path.display())),
    };
    if let Some(metadata) = existing {
        if !metadata.file_type().is_socket() || metadata.uid() != expected_uid {
            return Err("Refusing to replace a non-socket or foreign runtime object".to_string());
        }
        match UnixStream::connect(&socket_path) {
            Ok(_) => {
                return Err("Another remote dictation ingress is already listening".to_string())
            }
            Err(error) if error.kind() == io::ErrorKind::ConnectionRefused => {}
            Err(error) => {
                return Err(format!(
                    "Refusing ambiguous existing remote ingress socket: {error}"
                ))
            }
        }
        let identity = SocketIdentity::from_metadata(&metadata);
        let current = fs::symlink_metadata(&socket_path)
            .map_err(|error| format!("Cannot recheck stale socket: {error}"))?;
        if !current.file_type().is_socket() || !identity.matches(&current) {
            return Err("Remote ingress socket changed during stale-socket check".to_string());
        }
        fs::remove_file(&socket_path)
            .map_err(|error| format!("Failed to remove stale owner socket: {error}"))?;
    }
    Ok(socket_path)
}

fn remove_if_same_socket(path: &Path, identity: SocketIdentity) {
    if let Ok(metadata) = fs::symlink_metadata(path) {
        if metadata.file_type().is_socket() && identity.matches(&metadata) {
            let _ = fs::remove_file(path);
        }
    }
}

fn peer_uid(stream: &UnixStream) -> io::Result<u32> {
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
            (&mut credentials as *mut libc::ucred).cast(),
            &mut length,
        )
    };
    if result == 0 && length as usize == std::mem::size_of::<libc::ucred>() {
        Ok(credentials.uid)
    } else {
        Err(io::Error::last_os_error())
    }
}

fn peer_uid_allowed(expected_uid: u32, observed_uid: u32) -> bool {
    expected_uid == observed_uid
}

fn handle_connection(app: AppHandle, mut stream: UnixStream) -> Result<(), String> {
    let expected_uid = unsafe { libc::geteuid() };
    let observed_uid = peer_uid(&stream).map_err(|error| format!("SO_PEERCRED failed: {error}"))?;
    if !peer_uid_allowed(expected_uid, observed_uid) {
        return Err("Remote ingress peer is not the app user".to_string());
    }
    stream
        .set_read_timeout(Some(CONNECTION_IDLE_TIMEOUT))
        .map_err(|error| format!("Failed to set connection timeout: {error}"))?;
    let connection_id = NEXT_CONNECTION_ID.fetch_add(1, Ordering::Relaxed);
    let mut session = ConnectionSession::default();

    let result = (|| {
        loop {
            let payload = match read_frame(&mut stream, session.is_idle())? {
                FrameRead::Payload(payload) => payload,
                FrameRead::IdleTimeout => continue,
                FrameRead::Eof => break,
            };
            let request: WireRequest = serde_json::from_slice(&payload)
                .map_err(|error| format!("Malformed protocol message: {error}"))?;
            if request.version() != PROTOCOL_VERSION {
                let error = format!("Unsupported protocol version {}", request.version());
                write_response(&mut stream, &WireResponse::error(&error))?;
                return Err(error);
            }

            let response = dispatch_request(&app, connection_id, &mut session, request);
            match response {
                Ok(response) => write_response(&mut stream, &response)?,
                Err(error) => {
                    write_response(&mut stream, &WireResponse::error(&error))?;
                    return Err(error);
                }
            }
        }
        Ok(())
    })();

    if let Some(coordinator) = app.try_state::<TranscriptionCoordinator>() {
        coordinator.remote_disconnect(connection_id);
    }
    result
}

fn dispatch_request(
    app: &AppHandle,
    connection_id: u64,
    session: &mut ConnectionSession,
    request: WireRequest,
) -> Result<WireResponse, String> {
    let coordinator = app
        .try_state::<TranscriptionCoordinator>()
        .ok_or_else(|| "Transcription coordinator is unavailable".to_string())?;
    match request {
        WireRequest::Start {
            audio,
            delivery_mode,
            ..
        } => {
            audio.validate()?;
            let request_id = format!(
                "{:x}-{:x}",
                std::process::id(),
                NEXT_REQUEST_ID.fetch_add(1, Ordering::Relaxed)
            );
            session.begin(request_id.clone())?;
            coordinator.remote_start(connection_id, request_id, delivery_mode)?;
            let request_id = session.request_id.as_deref().expect("request was stored");
            Ok(WireResponse::status("recording", Some(request_id)))
        }
        WireRequest::Audio {
            request_id, pcm, ..
        } => {
            session.authorize(&request_id)?;
            coordinator.remote_audio(connection_id, request_id.clone(), pcm)?;
            Ok(WireResponse::status("accepted", Some(&request_id)))
        }
        WireRequest::Finish { request_id, .. } => {
            session.authorize(&request_id)?;
            coordinator.remote_finish(connection_id, request_id.clone())?;
            Ok(WireResponse::status("processing", Some(&request_id)))
        }
        WireRequest::Cancel { request_id, .. } => {
            session.authorize(&request_id)?;
            let status = coordinator.remote_cancel(connection_id, request_id.clone())?;
            cancel_response(session, &request_id, status)
        }
        WireRequest::Commit { request_id, .. } => {
            session.authorize(&request_id)?;
            let outcome = coordinator.remote_commit(connection_id, request_id.clone())?;
            commit_response(session, &request_id, outcome)
        }
        WireRequest::Status { request_id, .. } => {
            session.authorize(&request_id)?;
            let status = coordinator.remote_status(connection_id, request_id.clone())?;
            status_response(session, &request_id, status)
        }
    }
}

fn cancel_response(
    session: &mut ConnectionSession,
    request_id: &str,
    status: RemoteCancelStatus,
) -> Result<WireResponse, String> {
    let wire_status = match status {
        RemoteCancelStatus::Cancelled => {
            session.retire(request_id)?;
            "cancelled"
        }
        RemoteCancelStatus::Cancelling => "cancelling",
    };
    Ok(WireResponse::status(wire_status, Some(request_id)))
}

fn commit_response(
    session: &mut ConnectionSession,
    request_id: &str,
    outcome: RemoteCommitOutcome,
) -> Result<WireResponse, String> {
    if outcome.injection.is_some() && outcome.status != RemoteStatus::Succeeded {
        return Err("A failed remote commit cannot carry injection data".to_string());
    }
    let mut response = status_response(session, request_id, outcome.status)?;
    response.injection = outcome.injection;
    Ok(response)
}

fn status_response(
    session: &mut ConnectionSession,
    request_id: &str,
    status: RemoteStatus,
) -> Result<WireResponse, String> {
    let terminal = matches!(
        status,
        RemoteStatus::Succeeded | RemoteStatus::Failed | RemoteStatus::Cancelled
    );
    let wire_status = match &status {
        RemoteStatus::Recording => "recording",
        RemoteStatus::Processing => "processing",
        RemoteStatus::Ready => "ready",
        RemoteStatus::Cancelling => "cancelling",
        RemoteStatus::Succeeded => "succeeded",
        RemoteStatus::Failed => "failed",
        RemoteStatus::Cancelled => "cancelled",
    };
    let response = WireResponse::status(wire_status, Some(request_id));
    if terminal {
        session.retire(request_id)?;
    }
    Ok(response)
}

#[derive(Debug, PartialEq)]
enum FrameRead {
    Payload(Vec<u8>),
    IdleTimeout,
    Eof,
}

fn read_frame(reader: &mut impl Read, idle_timeout_allowed: bool) -> Result<FrameRead, String> {
    let mut header = [0u8; 4];
    loop {
        match reader.read(&mut header[..1]) {
            Ok(0) => return Ok(FrameRead::Eof),
            Ok(1) => break,
            Ok(_) => unreachable!(),
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error)
                if idle_timeout_allowed
                    && matches!(
                        error.kind(),
                        io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock
                    ) =>
            {
                return Ok(FrameRead::IdleTimeout)
            }
            Err(error)
                if matches!(
                    error.kind(),
                    io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock
                ) =>
            {
                return Err("Active remote request timed out".to_string())
            }
            Err(error) => return Err(format!("Failed to read protocol frame: {error}")),
        }
    }
    reader
        .read_exact(&mut header[1..])
        .map_err(|error| format!("Truncated protocol frame header: {error}"))?;
    let length = u32::from_be_bytes(header) as usize;
    if length == 0 || length > MAX_PROTOCOL_FRAME_BYTES {
        return Err(format!("Protocol frame length {length} is outside bounds"));
    }
    let mut payload = vec![0u8; length];
    reader
        .read_exact(&mut payload)
        .map_err(|error| format!("Truncated protocol frame payload: {error}"))?;
    Ok(FrameRead::Payload(payload))
}

fn write_response(writer: &mut impl Write, response: &WireResponse) -> Result<(), String> {
    let payload = serde_json::to_vec(response)
        .map_err(|error| format!("Failed to serialize protocol response: {error}"))?;
    if payload.len() > MAX_PROTOCOL_FRAME_BYTES {
        return Err("Protocol response exceeded frame bound".to_string());
    }
    writer
        .write_all(&(payload.len() as u32).to_be_bytes())
        .and_then(|()| writer.write_all(&payload))
        .and_then(|()| writer.flush())
        .map_err(|error| format!("Failed to write protocol response: {error}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;
    use tempfile::TempDir;

    fn framed(payload: &[u8]) -> Vec<u8> {
        let mut bytes = (payload.len() as u32).to_be_bytes().to_vec();
        bytes.extend_from_slice(payload);
        bytes
    }

    #[test]
    fn protocol_version_and_audio_format_are_exact() {
        let request: WireRequest = serde_json::from_slice(
            br#"{"type":"start","version":1,"audio":{"format":"s16le","sample_rate":16000,"channels":1}}"#,
        )
        .unwrap();
        assert_eq!(request.version(), PROTOCOL_VERSION);
        let WireRequest::Start {
            audio,
            delivery_mode,
            ..
        } = request
        else {
            panic!("expected start")
        };
        assert_eq!(audio.validate(), Ok(()));
        assert_eq!(delivery_mode, RemoteDeliveryMode::Herdr);

        let explicit_herdr: WireRequest = serde_json::from_slice(
            br#"{"type":"start","version":1,"delivery_mode":"herdr","audio":{"format":"s16le","sample_rate":16000,"channels":1}}"#,
        )
        .unwrap();
        assert!(matches!(
            explicit_herdr,
            WireRequest::Start {
                delivery_mode: RemoteDeliveryMode::Herdr,
                ..
            }
        ));

        let local: WireRequest = serde_json::from_slice(
            br#"{"type":"start","version":1,"delivery_mode":"local","audio":{"format":"s16le","sample_rate":16000,"channels":1}}"#,
        )
        .unwrap();
        assert!(matches!(
            local,
            WireRequest::Start {
                delivery_mode: RemoteDeliveryMode::Local,
                ..
            }
        ));
        assert!(serde_json::from_slice::<WireRequest>(
            br#"{"type":"start","version":1,"delivery_mode":"wayland","audio":{"format":"s16le","sample_rate":16000,"channels":1}}"#,
        )
        .is_err());

        for invalid in [
            AudioFormat {
                format: "f32le".into(),
                sample_rate: 16_000,
                channels: 1,
            },
            AudioFormat {
                format: "s16le".into(),
                sample_rate: 48_000,
                channels: 1,
            },
            AudioFormat {
                format: "s16le".into(),
                sample_rate: 16_000,
                channels: 2,
            },
        ] {
            assert!(invalid.validate().is_err());
        }
    }

    #[test]
    fn parser_refuses_unknown_fields_and_message_types() {
        assert!(serde_json::from_slice::<WireRequest>(
            br#"{"type":"status","version":1,"request_id":"r","extra":true}"#,
        )
        .is_err());
        assert!(
            serde_json::from_slice::<WireRequest>(br#"{"type":"guess","version":1}"#,).is_err()
        );
        assert!(serde_json::from_slice::<WireRequest>(
            br#"{"type":"bind","version":1,"pane_id":"wA:p1"}"#
        )
        .is_err());
        assert!(matches!(
            serde_json::from_slice::<WireRequest>(
                br#"{"type":"commit","version":1,"request_id":"r"}"#
            )
            .unwrap(),
            WireRequest::Commit { request_id, .. } if request_id == "r"
        ));
    }

    #[test]
    fn frame_reader_rejects_empty_oversized_and_truncated_frames() {
        assert!(read_frame(&mut Cursor::new(0u32.to_be_bytes()), true).is_err());
        assert!(read_frame(
            &mut Cursor::new(((MAX_PROTOCOL_FRAME_BYTES + 1) as u32).to_be_bytes()),
            true,
        )
        .is_err());
        assert!(read_frame(&mut Cursor::new(vec![0, 0, 0]), true).is_err());
        assert!(read_frame(&mut Cursor::new(framed(b"short")[..7].to_vec()), true).is_err());
    }

    #[test]
    fn frame_reader_accepts_one_bounded_frame_and_clean_eof() {
        let bytes = framed(br#"{"type":"status"}"#);
        let mut cursor = Cursor::new(bytes);
        assert_eq!(
            read_frame(&mut cursor, true).unwrap(),
            FrameRead::Payload(br#"{"type":"status"}"#.to_vec())
        );
        assert_eq!(read_frame(&mut cursor, true).unwrap(), FrameRead::Eof);
    }

    struct TimeoutReader {
        bytes: Cursor<Vec<u8>>,
    }

    impl TimeoutReader {
        fn after(bytes: impl Into<Vec<u8>>) -> Self {
            Self {
                bytes: Cursor::new(bytes.into()),
            }
        }
    }

    impl Read for TimeoutReader {
        fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
            if self.bytes.position() < self.bytes.get_ref().len() as u64 {
                self.bytes.read(buffer)
            } else {
                Err(io::Error::new(io::ErrorKind::TimedOut, "synthetic timeout"))
            }
        }
    }

    #[test]
    fn read_timeout_is_tolerated_only_between_idle_session_frames() {
        let mut idle_timeout = TimeoutReader::after([]);
        assert_eq!(
            read_frame(&mut idle_timeout, true).unwrap(),
            FrameRead::IdleTimeout
        );

        let mut active_timeout = TimeoutReader::after([]);
        assert!(read_frame(&mut active_timeout, false)
            .unwrap_err()
            .contains("Active remote request timed out"));

        let mut partial_header_timeout = TimeoutReader::after([0]);
        assert!(read_frame(&mut partial_header_timeout, true)
            .unwrap_err()
            .contains("Truncated protocol frame header"));

        let mut partial_payload_timeout = TimeoutReader::after(framed(b"short")[..7].to_vec());
        assert!(read_frame(&mut partial_payload_timeout, true)
            .unwrap_err()
            .contains("Truncated protocol frame payload"));
    }

    #[test]
    fn connection_session_retires_only_terminal_request_then_accepts_the_next() {
        let mut session = ConnectionSession::default();
        session.begin("request-a".into()).unwrap();
        assert_eq!(session.authorize("request-a"), Ok(()));
        assert!(session.authorize("request-b").is_err());
        assert!(session.begin("request-b".into()).is_err());

        status_response(&mut session, "request-a", RemoteStatus::Recording).unwrap();
        status_response(&mut session, "request-a", RemoteStatus::Processing).unwrap();
        status_response(&mut session, "request-a", RemoteStatus::Ready).unwrap();
        assert!(!session.is_idle());

        // A terminal commit response retires the exact request. A second commit
        // or any replay is then unauthorized before it can reach delivery.
        status_response(&mut session, "request-a", RemoteStatus::Succeeded).unwrap();
        assert!(session.is_idle());
        assert!(session.authorize("request-a").is_err());
        assert!(session.retire("request-a").is_err());
        session.begin("request-b".into()).unwrap();
        assert_eq!(session.authorize("request-b"), Ok(()));
        assert!(session.authorize("request-a").is_err());
    }

    #[test]
    fn local_injection_is_framed_only_on_one_consuming_owner_response() {
        let mut session = ConnectionSession::default();
        session.begin("request-a".into()).unwrap();

        let ordinary = status_response(&mut session, "request-a", RemoteStatus::Ready).unwrap();
        assert!(ordinary.injection.is_none());
        assert!(!session.is_idle());

        let plan = RemoteInjectionPlan {
            text: "bounded text ".to_string(),
            submit_key: Some(crate::settings::AutoSubmitKey::CtrlEnter),
        };
        let response = commit_response(
            &mut session,
            "request-a",
            RemoteCommitOutcome {
                status: RemoteStatus::Succeeded,
                injection: Some(plan.clone()),
            },
        )
        .unwrap();
        assert_eq!(response.injection, Some(plan));
        let mut frame = Vec::new();
        write_response(&mut frame, &response).unwrap();
        assert!(frame.len() <= MAX_PROTOCOL_FRAME_BYTES + 4);
        assert!(session.is_idle());
        assert!(session.authorize("request-a").is_err());

        let mut other_connection = ConnectionSession::default();
        other_connection.begin("request-b".into()).unwrap();
        assert!(commit_response(
            &mut other_connection,
            "request-a",
            RemoteCommitOutcome {
                status: RemoteStatus::Succeeded,
                injection: Some(RemoteInjectionPlan {
                    text: "must not return".to_string(),
                    submit_key: None,
                }),
            },
        )
        .is_err());

        let mut failed = ConnectionSession::default();
        failed.begin("request-failed".into()).unwrap();
        assert!(commit_response(
            &mut failed,
            "request-failed",
            RemoteCommitOutcome {
                status: RemoteStatus::Failed,
                injection: Some(RemoteInjectionPlan {
                    text: "must not accompany failure".to_string(),
                    submit_key: None,
                }),
            },
        )
        .is_err());
        let failed_response = commit_response(
            &mut failed,
            "request-failed",
            RemoteCommitOutcome {
                status: RemoteStatus::Failed,
                injection: None,
            },
        )
        .unwrap();
        assert!(failed_response.injection.is_none());
        assert!(failed.is_idle());

        let mut cancelled = ConnectionSession::default();
        cancelled.begin("request-cancelled".into()).unwrap();
        let cancelled_response =
            status_response(&mut cancelled, "request-cancelled", RemoteStatus::Cancelled)
                .unwrap();
        assert!(cancelled_response.injection.is_none());
        assert!(cancelled.is_idle());
    }

    #[test]
    fn cancellation_in_progress_retains_session_until_terminal_status() {
        let mut session = ConnectionSession::default();
        session.begin("request-a".into()).unwrap();
        let response =
            cancel_response(&mut session, "request-a", RemoteCancelStatus::Cancelling).unwrap();
        assert_eq!(response.status, "cancelling");
        assert!(!session.is_idle());

        status_response(&mut session, "request-a", RemoteStatus::Cancelling).unwrap();
        assert!(!session.is_idle());
        status_response(&mut session, "request-a", RemoteStatus::Cancelled).unwrap();
        assert!(session.is_idle());

        session.begin("request-b".into()).unwrap();
        let response =
            cancel_response(&mut session, "request-b", RemoteCancelStatus::Cancelled).unwrap();
        assert_eq!(response.status, "cancelled");
        assert!(session.is_idle());
    }

    #[test]
    fn peer_check_requires_exact_same_uid() {
        assert!(peer_uid_allowed(1000, 1000));
        assert!(!peer_uid_allowed(1000, 0));
        assert!(!peer_uid_allowed(1000, 1001));
    }

    fn runtime_dir() -> (TempDir, u32) {
        let temp = TempDir::new().unwrap();
        let uid = unsafe { libc::geteuid() };
        (temp, uid)
    }

    #[test]
    fn socket_path_removes_only_stale_owner_socket() {
        let (runtime, uid) = runtime_dir();
        let directory = runtime.path().join(SOCKET_DIRECTORY);
        fs::create_dir(&directory).unwrap();
        fs::set_permissions(&directory, fs::Permissions::from_mode(0o700)).unwrap();
        let socket = directory.join(SOCKET_FILE);
        let listener = UnixListener::bind(&socket).unwrap();
        drop(listener);

        assert_eq!(prepare_socket_path(runtime.path(), uid).unwrap(), socket);
        assert!(!socket.exists());
    }

    #[test]
    fn socket_path_refuses_live_listener_and_arbitrary_object() {
        let (runtime, uid) = runtime_dir();
        let directory = runtime.path().join(SOCKET_DIRECTORY);
        fs::create_dir(&directory).unwrap();
        fs::set_permissions(&directory, fs::Permissions::from_mode(0o700)).unwrap();
        let socket = directory.join(SOCKET_FILE);
        let _listener = UnixListener::bind(&socket).unwrap();
        assert!(prepare_socket_path(runtime.path(), uid).is_err());
        drop(_listener);
        fs::remove_file(&socket).unwrap();
        fs::write(&socket, b"not a socket").unwrap();
        assert!(prepare_socket_path(runtime.path(), uid).is_err());
        assert_eq!(fs::read(&socket).unwrap(), b"not a socket");
    }

    #[test]
    fn socket_directory_must_be_owner_only_and_not_a_symlink() {
        let (runtime, uid) = runtime_dir();
        let directory = runtime.path().join(SOCKET_DIRECTORY);
        fs::create_dir(&directory).unwrap();
        fs::set_permissions(&directory, fs::Permissions::from_mode(0o755)).unwrap();
        assert!(prepare_socket_path(runtime.path(), uid).is_err());
    }
}
