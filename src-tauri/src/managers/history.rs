use anyhow::{anyhow, Result};
use chrono::{DateTime, Local, Utc};
use log::{debug, error, info};
use rusqlite::{params, Connection, OptionalExtension};
use rusqlite_migration::{Migrations, M};
use serde::{Deserialize, Serialize};
use specta::Type;
use std::collections::HashSet;
use std::ffi::OsString;
use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use tauri::{AppHandle, Manager};
use tauri_specta::Event;

const TEXT_PAIR_LIMIT: usize = 1_000;
const WAV_RESERVATION_ATTEMPTS: u64 = 64;
static NEXT_WAV_NONCE: AtomicU64 = AtomicU64::new(0);

#[derive(Default)]
struct CleanupReport {
    entries_updated: Vec<i64>,
    entries_deleted: Vec<i64>,
}

#[derive(Debug)]
struct CleanupFilesystemError {
    failures: Vec<String>,
}

impl fmt::Display for CleanupFilesystemError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            formatter,
            "Failed to remove {} retained audio file(s): {}",
            self.failures.len(),
            self.failures.join("; ")
        )
    }
}

impl std::error::Error for CleanupFilesystemError {}

/// Database migrations for transcription history.
/// Each migration is applied in order. The library tracks which migrations
/// have been applied using SQLite's user_version pragma.
///
/// Note: For users upgrading from tauri-plugin-sql, migrate_from_tauri_plugin_sql()
/// converts the old _sqlx_migrations table tracking to the user_version pragma,
/// ensuring migrations don't re-run on existing databases.
static MIGRATIONS: &[M] = &[
    M::up(
        "CREATE TABLE IF NOT EXISTS transcription_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_name TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            saved BOOLEAN NOT NULL DEFAULT 0,
            title TEXT NOT NULL,
            transcription_text TEXT NOT NULL
        );",
    ),
    M::up("ALTER TABLE transcription_history ADD COLUMN post_processed_text TEXT;"),
    M::up("ALTER TABLE transcription_history ADD COLUMN post_process_prompt TEXT;"),
    M::up("ALTER TABLE transcription_history ADD COLUMN post_process_requested BOOLEAN NOT NULL DEFAULT 0;"),
    M::up("ALTER TABLE transcription_history ADD COLUMN post_process_model TEXT;"),
    M::up(
        "ALTER TABLE transcription_history ADD COLUMN audio_available BOOLEAN NOT NULL DEFAULT 0;",
    ),
];

#[derive(Clone, Debug, Serialize, Deserialize, Type)]
pub struct PaginatedHistory {
    pub entries: Vec<HistoryEntry>,
    pub has_more: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize, Type, tauri_specta::Event)]
#[serde(tag = "action")]
pub enum HistoryUpdatePayload {
    #[serde(rename = "added")]
    Added { entry: HistoryEntry },
    #[serde(rename = "updated")]
    Updated { entry: HistoryEntry },
    #[serde(rename = "deleted")]
    Deleted { id: i64 },
    #[serde(rename = "toggled")]
    Toggled { id: i64 },
}

#[derive(Clone, Debug, Serialize, Deserialize, Type)]
pub struct HistoryEntry {
    pub id: i64,
    pub file_name: String,
    pub timestamp: i64,
    pub saved: bool,
    pub title: String,
    pub transcription_text: String,
    pub post_processed_text: Option<String>,
    pub post_process_prompt: Option<String>,
    pub post_process_model: Option<String>,
    pub post_process_requested: bool,
    pub audio_available: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct ReservedFileIdentity {
    device: u64,
    inode: u64,
}

impl ReservedFileIdentity {
    fn from_metadata(metadata: &fs::Metadata) -> Self {
        use std::os::unix::fs::MetadataExt;
        Self {
            device: metadata.dev(),
            inode: metadata.ino(),
        }
    }

    fn matches(self, metadata: &fs::Metadata) -> bool {
        if !metadata.file_type().is_file() {
            return false;
        }
        use std::os::unix::fs::MetadataExt;
        self.device == metadata.dev() && self.inode == metadata.ino()
    }
}

pub(crate) struct PendingAudioGuard<'a> {
    pending_audio_files: &'a Mutex<HashSet<OsString>>,
    file_name: OsString,
    path: PathBuf,
    identity: ReservedFileIdentity,
    writer: Option<File>,
    history_owned: bool,
}

impl PendingAudioGuard<'_> {
    pub(crate) fn path(&self) -> &Path {
        &self.path
    }

    pub(crate) fn take_writer(&mut self) -> Result<File> {
        self.writer
            .take()
            .ok_or_else(|| anyhow!("reserved WAV writer was already taken"))
    }

    fn is_exact_reserved_file(&self) -> bool {
        fs::symlink_metadata(&self.path).is_ok_and(|metadata| self.identity.matches(&metadata))
    }

    fn mark_history_owned(&mut self) {
        self.history_owned = true;
    }
}

impl Drop for PendingAudioGuard<'_> {
    fn drop(&mut self) {
        // Close the exclusive writer before attempting teardown on platforms
        // that do not permit unlinking an open file.
        self.writer.take();
        if !self.history_owned {
            match fs::symlink_metadata(&self.path) {
                Ok(metadata) if self.identity.matches(&metadata) => {
                    match fs::remove_file(&self.path) {
                        Ok(()) => {
                            debug!("Deleted uncommitted WAV file: {}", self.path.display())
                        }
                        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                        Err(error) => error!(
                            "Failed to delete uncommitted WAV file {}: {}",
                            self.path.display(),
                            error
                        ),
                    }
                }
                Ok(_) => error!(
                    "Refusing to delete replaced uncommitted WAV path {}",
                    self.path.display()
                ),
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(error) => error!(
                    "Failed to inspect uncommitted WAV file {}: {}",
                    self.path.display(),
                    error
                ),
            }
        }
        self.pending_audio_files
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .remove(&self.file_name);
    }
}

fn wav_candidate_name(timestamp_millis: i64, process_id: u32, nonce: u64) -> String {
    format!("handy-{timestamp_millis}-{process_id}-{nonce:016x}.wav")
}

fn is_safe_wav_candidate(file_name: &str) -> bool {
    let path = Path::new(file_name);
    path.file_name() == Some(path.as_os_str())
        && path
            .extension()
            .and_then(|extension| extension.to_str())
            .is_some_and(|extension| extension.eq_ignore_ascii_case("wav"))
}

fn reserve_pending_audio_from_candidates<'a>(
    recordings_dir: &Path,
    pending_audio_files: &'a Mutex<HashSet<OsString>>,
    candidates: impl IntoIterator<Item = String>,
) -> Result<PendingAudioGuard<'a>> {
    // Cleanup takes this same mutex before scanning. Keep it held from before
    // create_new through pending registration so the new inode is never
    // observable as an unowned orphan.
    let mut pending = pending_audio_files
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);

    for candidate in candidates {
        if !is_safe_wav_candidate(&candidate) {
            return Err(anyhow!("unsafe WAV reservation candidate"));
        }
        let file_name = OsString::from(candidate);
        if pending.contains(&file_name) {
            continue;
        }
        let path = recordings_dir.join(&file_name);
        use std::os::unix::fs::OpenOptionsExt;
        let mut options = OpenOptions::new();
        options.read(true).write(true).create_new(true).mode(0o600);

        let writer = match options.open(&path) {
            Ok(writer) => writer,
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => {
                return Err(anyhow!(
                    "Failed to reserve WAV file {}: {}",
                    path.display(),
                    error
                ))
            }
        };
        use std::os::unix::fs::PermissionsExt;
        if let Err(error) = writer.set_permissions(fs::Permissions::from_mode(0o600)) {
            drop(writer);
            let _ = fs::remove_file(&path);
            return Err(anyhow!(
                "Failed to secure reserved WAV file {}: {}",
                path.display(),
                error
            ));
        }
        let metadata = writer.metadata().map_err(|error| {
            let _ = fs::remove_file(&path);
            anyhow!(
                "Failed to inspect reserved WAV file {}: {}",
                path.display(),
                error
            )
        })?;
        let identity = ReservedFileIdentity::from_metadata(&metadata);
        pending.insert(file_name.clone());
        return Ok(PendingAudioGuard {
            pending_audio_files,
            file_name,
            path,
            identity,
            writer: Some(writer),
            history_owned: false,
        });
    }

    Err(anyhow!(
        "Could not reserve a unique WAV file after bounded attempts"
    ))
}

pub struct HistoryManager {
    app_handle: AppHandle,
    recordings_dir: PathBuf,
    db_path: PathBuf,
    pending_audio_files: Mutex<HashSet<OsString>>,
}

impl HistoryManager {
    pub fn new(app_handle: &AppHandle) -> Result<Self> {
        // Create recordings directory in app data dir
        let app_data_dir = app_handle.path().app_data_dir()?;
        let recordings_dir = app_data_dir.join("recordings");
        let db_path = app_data_dir.join("history.db");

        // Ensure recordings directory exists
        if !recordings_dir.exists() {
            fs::create_dir_all(&recordings_dir)?;
            debug!("Created recordings directory: {:?}", recordings_dir);
        }

        let manager = Self {
            app_handle: app_handle.clone(),
            recordings_dir,
            db_path,
            pending_audio_files: Mutex::new(HashSet::new()),
        };

        // Initialize database, migrate and reconcile in place, then enforce the
        // independent audio and text bounds rather than waiting for a recording.
        manager.init_database()?;
        manager.cleanup_tolerating_filesystem_error("history startup")?;

        Ok(manager)
    }

    fn init_database(&self) -> Result<()> {
        info!("Initializing database at {:?}", self.db_path);

        let mut conn = Connection::open(&self.db_path)?;

        // Handle migration from tauri-plugin-sql to rusqlite_migration
        // tauri-plugin-sql used _sqlx_migrations table, rusqlite_migration uses user_version pragma
        self.migrate_from_tauri_plugin_sql(&conn)?;

        // Create migrations object and run to latest version
        let migrations = Migrations::new(MIGRATIONS.to_vec());

        // Validate migrations in debug builds
        #[cfg(debug_assertions)]
        migrations.validate().expect("Invalid migrations");

        // Get current version before migration
        let version_before: i32 =
            conn.pragma_query_value(None, "user_version", |row| row.get(0))?;
        debug!("Database version before migration: {}", version_before);

        // Apply any pending migrations
        migrations.to_latest(&mut conn)?;

        // Get version after migration
        let version_after: i32 = conn.pragma_query_value(None, "user_version", |row| row.get(0))?;

        if version_after > version_before {
            info!(
                "Database migrated from version {} to {}",
                version_before, version_after
            );
        } else {
            debug!("Database already at latest version {}", version_after);
        }

        // Migration defaults cannot truthfully describe historical files. Check
        // every row before startup cleanup or any UI-facing query can use it.
        Self::reconcile_audio_availability_with_conn(&conn, &self.recordings_dir)?;

        Ok(())
    }

    /// Migrate from tauri-plugin-sql's migration tracking to rusqlite_migration's.
    /// tauri-plugin-sql used a _sqlx_migrations table, while rusqlite_migration uses
    /// SQLite's user_version pragma. This function checks if the old system was in use
    /// and sets the user_version accordingly so migrations don't re-run.
    fn migrate_from_tauri_plugin_sql(&self, conn: &Connection) -> Result<()> {
        // Check if the old _sqlx_migrations table exists
        let has_sqlx_migrations: bool = conn
            .query_row(
                "SELECT COUNT(*) > 0 FROM sqlite_master WHERE type='table' AND name='_sqlx_migrations'",
                [],
                |row| row.get(0),
            )
            .unwrap_or(false);

        if !has_sqlx_migrations {
            return Ok(());
        }

        // Check current user_version
        let current_version: i32 =
            conn.pragma_query_value(None, "user_version", |row| row.get(0))?;

        if current_version > 0 {
            // Already migrated to rusqlite_migration system
            return Ok(());
        }

        // Get the highest version from the old migrations table
        let old_version: i32 = conn
            .query_row(
                "SELECT COALESCE(MAX(version), 0) FROM _sqlx_migrations WHERE success = 1",
                [],
                |row| row.get(0),
            )
            .unwrap_or(0);

        if old_version > 0 {
            info!(
                "Migrating from tauri-plugin-sql (version {}) to rusqlite_migration",
                old_version
            );

            // Set user_version to match the old migration state
            conn.pragma_update(None, "user_version", old_version)?;

            // Optionally drop the old migrations table (keeping it doesn't hurt)
            // conn.execute("DROP TABLE IF EXISTS _sqlx_migrations", [])?;

            info!(
                "Migration tracking converted: user_version set to {}",
                old_version
            );
        }

        Ok(())
    }

    fn get_connection(&self) -> Result<Connection> {
        Ok(Connection::open(&self.db_path)?)
    }

    fn is_regular_wav_file(recordings_dir: &Path, file_name: &str) -> bool {
        let relative_path = Path::new(file_name);
        let is_single_file_name = relative_path.file_name() == Some(relative_path.as_os_str());
        let has_wav_extension = relative_path
            .extension()
            .and_then(|extension| extension.to_str())
            .is_some_and(|extension| extension.eq_ignore_ascii_case("wav"));

        is_single_file_name
            && has_wav_extension
            && fs::symlink_metadata(recordings_dir.join(relative_path))
                .is_ok_and(|metadata| metadata.file_type().is_file())
    }

    fn reconcile_audio_availability_with_conn(
        conn: &Connection,
        recordings_dir: &Path,
    ) -> Result<()> {
        let mut stmt = conn.prepare(
            "SELECT id, file_name, audio_available
             FROM transcription_history",
        )?;
        let entries = stmt
            .query_map([], |row| {
                Ok((
                    row.get::<_, i64>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, bool>(2)?,
                ))
            })?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        drop(stmt);

        for (id, file_name, recorded_availability) in entries {
            let audio_available = Self::is_regular_wav_file(recordings_dir, &file_name);
            if audio_available != recorded_availability {
                conn.execute(
                    "UPDATE transcription_history
                     SET audio_available = ?1
                     WHERE id = ?2",
                    params![audio_available, id],
                )?;
            }
        }

        Ok(())
    }

    pub(crate) fn reserve_pending_audio_file(&self) -> Result<PendingAudioGuard<'_>> {
        let timestamp_millis = Utc::now().timestamp_millis();
        let process_id = std::process::id();
        let first_nonce = NEXT_WAV_NONCE.fetch_add(WAV_RESERVATION_ATTEMPTS, Ordering::Relaxed);
        let candidates = (0..WAV_RESERVATION_ATTEMPTS).map(|offset| {
            wav_candidate_name(
                timestamp_millis,
                process_id,
                first_nonce.wrapping_add(offset),
            )
        });
        reserve_pending_audio_from_candidates(
            &self.recordings_dir,
            &self.pending_audio_files,
            candidates,
        )
    }

    fn cleanup_orphaned_wav_files_with_conn(
        conn: &Connection,
        recordings_dir: &Path,
        pending_audio_files: &Mutex<HashSet<OsString>>,
    ) -> Result<()> {
        // Registration uses the same lock before publishing a WAV, so a scan
        // can never observe a new file without also observing its pending name.
        let pending_audio_files = pending_audio_files
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let mut stmt = conn.prepare("SELECT file_name FROM transcription_history")?;
        let tracked_files = stmt
            .query_map([], |row| row.get::<_, String>(0).map(OsString::from))?
            .collect::<std::result::Result<HashSet<_>, _>>()?;
        drop(stmt);

        let directory_entries = fs::read_dir(recordings_dir).map_err(|error| {
            anyhow!(CleanupFilesystemError {
                failures: vec![format!("{}: {}", recordings_dir.display(), error)],
            })
        })?;
        let mut failures = Vec::new();

        for entry_result in directory_entries {
            let entry = match entry_result {
                Ok(entry) => entry,
                Err(error) => {
                    failures.push(format!("{}: {}", recordings_dir.display(), error));
                    continue;
                }
            };
            let file_type = match entry.file_type() {
                Ok(file_type) => file_type,
                Err(error) => {
                    failures.push(format!("{}: {}", entry.path().display(), error));
                    continue;
                }
            };
            if !file_type.is_file()
                || tracked_files.contains(&entry.file_name())
                || pending_audio_files.contains(&entry.file_name())
            {
                continue;
            }

            let is_wav = entry
                .path()
                .extension()
                .and_then(|extension| extension.to_str())
                .is_some_and(|extension| extension.eq_ignore_ascii_case("wav"));
            if !is_wav {
                continue;
            }

            // Recheck immediately before unlinking in case another SQLite
            // connection committed a row after the initial query snapshot.
            let tracked_now = entry
                .file_name()
                .to_str()
                .map(|file_name| {
                    conn.query_row(
                        "SELECT EXISTS(
                            SELECT 1 FROM transcription_history WHERE file_name = ?1
                         )",
                        params![file_name],
                        |row| row.get::<_, bool>(0),
                    )
                })
                .transpose()?
                .unwrap_or(false);
            if tracked_now {
                continue;
            }

            match fs::remove_file(entry.path()) {
                Ok(()) => debug!("Deleted untracked WAV file: {}", entry.path().display()),
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(error) => {
                    error!(
                        "Failed to delete untracked WAV file {}: {}",
                        entry.path().display(),
                        error
                    );
                    failures.push(format!("{}: {}", entry.path().display(), error));
                }
            }
        }

        if failures.is_empty() {
            Ok(())
        } else {
            Err(anyhow!(CleanupFilesystemError { failures }))
        }
    }

    fn cleanup_tolerating_filesystem_error(&self, context: &str) -> Result<()> {
        let conn = self.get_connection()?;
        let mut filesystem_failures = Vec::new();
        let mut collect_cleanup_result = |result: Result<()>| -> Result<()> {
            match result {
                Ok(()) => Ok(()),
                Err(cleanup_error) => match cleanup_error.downcast::<CleanupFilesystemError>() {
                    Ok(cleanup_error) => {
                        filesystem_failures.extend(cleanup_error.failures);
                        Ok(())
                    }
                    Err(cleanup_error) => Err(cleanup_error),
                },
            }
        };

        collect_cleanup_result(Self::cleanup_orphaned_wav_files_with_conn(
            &conn,
            &self.recordings_dir,
            &self.pending_audio_files,
        ))?;
        collect_cleanup_result(self.cleanup_old_entries())?;

        if !filesystem_failures.is_empty() {
            let cleanup_error = CleanupFilesystemError {
                failures: filesystem_failures,
            };
            error!(
                "History cleanup could not remove retained audio during {}: {}. Cleanup will retry later.",
                context, cleanup_error
            );
        }

        Ok(())
    }

    fn map_history_entry(row: &rusqlite::Row<'_>) -> rusqlite::Result<HistoryEntry> {
        Ok(HistoryEntry {
            id: row.get("id")?,
            file_name: row.get("file_name")?,
            timestamp: row.get("timestamp")?,
            saved: row.get("saved")?,
            title: row.get("title")?,
            transcription_text: row.get("transcription_text")?,
            post_processed_text: row.get("post_processed_text")?,
            post_process_prompt: row.get("post_process_prompt")?,
            post_process_model: row.get("post_process_model")?,
            post_process_requested: row.get("post_process_requested")?,
            audio_available: row.get("audio_available")?,
        })
    }

    /// Atomically transfers a published WAV from the teardown guard to
    /// history ownership as soon as its database row is inserted.
    pub(crate) fn save_pending_entry(
        &self,
        pending: &mut PendingAudioGuard<'_>,
        transcription_text: String,
    ) -> Result<HistoryEntry> {
        let file_name = pending.file_name.to_string_lossy().into_owned();
        if pending.path != self.recordings_dir.join(&file_name) || !pending.is_exact_reserved_file()
        {
            return Err(anyhow!(
                "pending WAV does not belong to this history manager"
            ));
        }
        self.save_entry_with_ownership(
            file_name,
            transcription_text,
            || pending.mark_history_owned(),
        )
    }

    fn save_entry_with_ownership(
        &self,
        file_name: String,
        transcription_text: String,
        history_owned: impl FnOnce(),
    ) -> Result<HistoryEntry> {
        let timestamp = Utc::now().timestamp();
        let title = self.format_timestamp_title(timestamp);
        let audio_available = Self::is_regular_wav_file(&self.recordings_dir, &file_name);

        let conn = self.get_connection()?;
        conn.execute(
            "INSERT INTO transcription_history (
                file_name,
                timestamp,
                saved,
                title,
                transcription_text,
                post_processed_text,
                post_process_prompt,
                post_process_model,
                post_process_requested,
                audio_available
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
            params![
                &file_name,
                timestamp,
                false,
                &title,
                &transcription_text,
                Option::<String>::None,
                Option::<String>::None,
                Option::<String>::None,
                false,
                audio_available,
            ],
        )?;
        // From this point the database owns the WAV. Mark the publication
        // guard before policy cleanup so a later cleanup error cannot cause
        // Drop to delete audio still recorded as available in history.
        history_owned();

        let mut entry = HistoryEntry {
            id: conn.last_insert_rowid(),
            file_name,
            timestamp,
            saved: false,
            title,
            transcription_text,
            post_processed_text: None,
            post_process_prompt: None,
            post_process_model: None,
            post_process_requested: false,
            audio_available,
        };

        debug!("Saved history entry with id {}", entry.id);

        self.cleanup_tolerating_filesystem_error("history save")?;

        // Count policy zero can immediately remove an incomplete/raw-only row.
        // Do not add such a row to the UI after cleanup has deleted it.
        if let Some(retained_entry) = Self::get_entry_by_id_with_conn(&conn, entry.id)? {
            entry = retained_entry;
            if let Err(e) = (HistoryUpdatePayload::Added {
                entry: entry.clone(),
            })
            .emit(&self.app_handle)
            {
                error!("Failed to emit history-updated event: {}", e);
            }
        }

        Ok(entry)
    }

    /// Update an existing history entry with new transcription results (used by retry).
    pub fn update_transcription(
        &self,
        id: i64,
        transcription_text: String,
    ) -> Result<HistoryEntry> {
        let conn = self.get_connection()?;
        let updated = conn.execute(
            "UPDATE transcription_history
             SET transcription_text = ?1,
                 post_processed_text = NULL,
                 post_process_prompt = NULL,
                 post_process_model = NULL,
                 post_process_requested = 0
             WHERE id = ?2",
            params![transcription_text, id],
        )?;

        if updated == 0 {
            return Err(anyhow!("History entry {} not found", id));
        }

        let entry = Self::get_entry_by_id_with_conn(&conn, id)?
            .ok_or_else(|| anyhow!("History entry {} not found after update", id))?;

        debug!("Updated transcription for history entry {}", id);

        self.cleanup_tolerating_filesystem_error("history transcription update")?;

        // A retry of an old row can make it the 1,001st pair, in which case
        // retention removes it immediately according to its original timestamp.
        if let Some(retained_entry) = Self::get_entry_by_id_with_conn(&conn, id)? {
            if let Err(e) = (HistoryUpdatePayload::Updated {
                entry: retained_entry.clone(),
            })
            .emit(&self.app_handle)
            {
                error!("Failed to emit history-updated event: {}", e);
            }
            Ok(retained_entry)
        } else {
            Ok(entry)
        }
    }

    pub fn cleanup_old_entries(&self) -> Result<()> {
        let retention_period = crate::settings::get_recording_retention_period(&self.app_handle);
        let conn = self.get_connection()?;
        let mut report = CleanupReport::default();

        let audio_cleanup_result = match retention_period {
            crate::settings::RecordingRetentionPeriod::Never => Ok(()),
            crate::settings::RecordingRetentionPeriod::PreserveLimit => {
                let limit = crate::settings::get_history_limit(&self.app_handle);
                Self::cleanup_audio_by_count_with_conn(
                    &conn,
                    &self.recordings_dir,
                    limit,
                    &mut report,
                )
            }
            retention_period => {
                let now = Utc::now().timestamp();
                let cutoff_timestamp = match retention_period {
                    crate::settings::RecordingRetentionPeriod::Days3 => now - (3 * 24 * 60 * 60),
                    crate::settings::RecordingRetentionPeriod::Weeks2 => {
                        now - (2 * 7 * 24 * 60 * 60)
                    }
                    crate::settings::RecordingRetentionPeriod::Months3 => {
                        now - (3 * 30 * 24 * 60 * 60)
                    }
                    _ => unreachable!("All retention variants handled above"),
                };
                Self::cleanup_audio_by_time_with_conn(
                    &conn,
                    &self.recordings_dir,
                    cutoff_timestamp,
                    &mut report,
                )
            }
        };

        // Filesystem failures do not stop independent text cleanup or truthful
        // UI events. Database failures remain immediate, real errors.
        let filesystem_error = match audio_cleanup_result {
            Ok(()) => None,
            Err(cleanup_error)
                if cleanup_error
                    .downcast_ref::<CleanupFilesystemError>()
                    .is_some() =>
            {
                Some(cleanup_error)
            }
            Err(cleanup_error) => return Err(cleanup_error),
        };

        // Text-pair retention is independent of the WAV policy. Incomplete rows
        // disappear only after audio does; expired successful pairs retain any
        // policy-retained audio row but lose all private pair text and metadata.
        Self::cleanup_text_rows_with_conn(&conn, TEXT_PAIR_LIMIT, &mut report)?;
        self.emit_cleanup_report(&conn, &report)?;

        if let Some(filesystem_error) = filesystem_error {
            return Err(filesystem_error);
        }

        Ok(())
    }

    fn cleanup_audio_by_count_with_conn(
        conn: &Connection,
        recordings_dir: &Path,
        limit: usize,
        report: &mut CleanupReport,
    ) -> Result<()> {
        let mut stmt = conn.prepare(
            "SELECT id, file_name
             FROM transcription_history
             WHERE audio_available = 1
             ORDER BY timestamp DESC, id DESC
             LIMIT -1 OFFSET ?1",
        )?;
        let entries = stmt
            .query_map(params![limit as i64], |row| {
                Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?))
            })?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        drop(stmt);

        Self::remove_audio_for_entries(conn, recordings_dir, &entries, report)
    }

    fn cleanup_audio_by_time_with_conn(
        conn: &Connection,
        recordings_dir: &Path,
        cutoff_timestamp: i64,
        report: &mut CleanupReport,
    ) -> Result<()> {
        let mut stmt = conn.prepare(
            "SELECT id, file_name
             FROM transcription_history
             WHERE saved = 0 AND audio_available = 1 AND timestamp < ?1",
        )?;
        let entries = stmt
            .query_map(params![cutoff_timestamp], |row| {
                Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?))
            })?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        drop(stmt);

        Self::remove_audio_for_entries(conn, recordings_dir, &entries, report)
    }

    fn remove_audio_for_entries(
        conn: &Connection,
        recordings_dir: &Path,
        entries: &[(i64, String)],
        report: &mut CleanupReport,
    ) -> Result<()> {
        let mut failures = Vec::new();

        for (id, file_name) in entries {
            let file_path = recordings_dir.join(file_name);
            match fs::remove_file(&file_path) {
                Ok(()) => debug!("Deleted old WAV file: {}", file_name),
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(error) => {
                    // Keep the row and marker linked to the obstructed path. A
                    // later cleanup will retry after the filesystem permits it.
                    error!("Failed to delete old WAV file {}: {}", file_name, error);
                    failures.push(format!("{}: {}", file_path.display(), error));
                    continue;
                }
            }

            let updated = conn.execute(
                "UPDATE transcription_history
                 SET audio_available = 0
                 WHERE id = ?1 AND audio_available = 1",
                params![id],
            )?;
            if updated > 0 {
                report.entries_updated.push(*id);
            }
        }

        if failures.is_empty() {
            Ok(())
        } else {
            Err(anyhow!(CleanupFilesystemError { failures }))
        }
    }

    fn cleanup_text_rows_with_conn(
        conn: &Connection,
        pair_limit: usize,
        report: &mut CleanupReport,
    ) -> Result<()> {
        // A corpus pair is a successful second pass with non-empty raw text,
        // output, and exact prompt. Historical pairs may have no model
        // identity because that fact cannot be truthfully backfilled.
        let mut stmt = conn.prepare(
            "SELECT id
             FROM transcription_history
             WHERE audio_available = 0
               AND NOT (
                   transcription_text != ''
                   AND COALESCE(post_processed_text, '') != ''
                   AND COALESCE(post_process_prompt, '') != ''
               )",
        )?;
        let incomplete_entries = stmt
            .query_map([], |row| row.get::<_, i64>(0))?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        drop(stmt);
        Self::delete_database_entries(conn, &incomplete_entries, report)?;

        let mut stmt = conn.prepare(
            "SELECT id, audio_available
             FROM transcription_history
             WHERE transcription_text != ''
               AND COALESCE(post_processed_text, '') != ''
               AND COALESCE(post_process_prompt, '') != ''
             ORDER BY timestamp DESC, id DESC
             LIMIT -1 OFFSET ?1",
        )?;
        let expired_pairs = stmt
            .query_map(params![pair_limit as i64], |row| {
                Ok((row.get::<_, i64>(0)?, row.get::<_, bool>(1)?))
            })?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        drop(stmt);

        for (id, audio_available) in expired_pairs {
            if audio_available {
                let updated = conn.execute(
                    "UPDATE transcription_history
                     SET transcription_text = '',
                         post_processed_text = NULL,
                         post_process_prompt = NULL,
                         post_process_model = NULL,
                         post_process_requested = 0
                     WHERE id = ?1 AND audio_available = 1",
                    params![id],
                )?;
                if updated > 0 {
                    report.entries_updated.push(id);
                }
            } else {
                Self::delete_database_entries(conn, &[id], report)?;
            }
        }

        Ok(())
    }

    fn delete_database_entries(
        conn: &Connection,
        entries: &[i64],
        report: &mut CleanupReport,
    ) -> Result<()> {
        for id in entries {
            if conn.execute(
                "DELETE FROM transcription_history WHERE id = ?1",
                params![id],
            )? > 0
            {
                report.entries_deleted.push(*id);
            }
        }

        Ok(())
    }

    fn emit_cleanup_report(&self, conn: &Connection, report: &CleanupReport) -> Result<()> {
        for id in &report.entries_updated {
            if let Some(entry) = Self::get_entry_by_id_with_conn(conn, *id)? {
                if let Err(error) = (HistoryUpdatePayload::Updated { entry }).emit(&self.app_handle)
                {
                    error!("Failed to emit history-updated event: {}", error);
                }
            }
        }

        for id in &report.entries_deleted {
            if let Err(error) = (HistoryUpdatePayload::Deleted { id: *id }).emit(&self.app_handle) {
                error!("Failed to emit history-updated event: {}", error);
            }
        }

        Ok(())
    }

    pub async fn get_history_entries(
        &self,
        cursor: Option<i64>,
        limit: Option<usize>,
    ) -> Result<PaginatedHistory> {
        let conn = self.get_connection()?;
        let limit = limit.map(|l| l.min(100));

        let mut entries: Vec<HistoryEntry> = match (cursor, limit) {
            (Some(cursor_id), Some(lim)) => {
                let fetch_count = (lim + 1) as i64;
                let mut stmt = conn.prepare(
                    "SELECT id, file_name, timestamp, saved, title, transcription_text, post_processed_text, post_process_prompt, post_process_model, post_process_requested, audio_available
                     FROM transcription_history
                     WHERE id < ?1
                     ORDER BY id DESC
                     LIMIT ?2",
                )?;
                let result = stmt
                    .query_map(params![cursor_id, fetch_count], Self::map_history_entry)?
                    .collect::<std::result::Result<Vec<_>, _>>()?;
                result
            }
            (None, Some(lim)) => {
                let fetch_count = (lim + 1) as i64;
                let mut stmt = conn.prepare(
                    "SELECT id, file_name, timestamp, saved, title, transcription_text, post_processed_text, post_process_prompt, post_process_model, post_process_requested, audio_available
                     FROM transcription_history
                     ORDER BY id DESC
                     LIMIT ?1",
                )?;
                let result = stmt
                    .query_map(params![fetch_count], Self::map_history_entry)?
                    .collect::<std::result::Result<Vec<_>, _>>()?;
                result
            }
            (_, None) => {
                let mut stmt = conn.prepare(
                    "SELECT id, file_name, timestamp, saved, title, transcription_text, post_processed_text, post_process_prompt, post_process_model, post_process_requested, audio_available
                     FROM transcription_history
                     ORDER BY id DESC",
                )?;
                let result = stmt
                    .query_map([], Self::map_history_entry)?
                    .collect::<std::result::Result<Vec<_>, _>>()?;
                result
            }
        };

        let has_more = limit.is_some_and(|lim| entries.len() > lim);
        if has_more {
            entries.pop();
        }

        Ok(PaginatedHistory { entries, has_more })
    }

    #[cfg(test)]
    fn get_latest_entry_with_conn(conn: &Connection) -> Result<Option<HistoryEntry>> {
        let mut stmt = conn.prepare(
            "SELECT
                id,
                file_name,
                timestamp,
                saved,
                title,
                transcription_text,
                post_processed_text,
                post_process_prompt,
                post_process_model,
                post_process_requested,
                audio_available
             FROM transcription_history
             ORDER BY timestamp DESC, id DESC
             LIMIT 1",
        )?;

        let entry = stmt.query_row([], Self::map_history_entry).optional()?;
        Ok(entry)
    }

    /// Get the latest entry with non-empty transcription text.
    pub fn get_latest_completed_entry(&self) -> Result<Option<HistoryEntry>> {
        let conn = self.get_connection()?;
        Self::get_latest_completed_entry_with_conn(&conn)
    }

    fn get_latest_completed_entry_with_conn(conn: &Connection) -> Result<Option<HistoryEntry>> {
        let mut stmt = conn.prepare(
            "SELECT
                id,
                file_name,
                timestamp,
                saved,
                title,
                transcription_text,
                post_processed_text,
                post_process_prompt,
                post_process_model,
                post_process_requested,
                audio_available
             FROM transcription_history
             WHERE transcription_text != ''
             ORDER BY timestamp DESC, id DESC
             LIMIT 1",
        )?;

        let entry = stmt.query_row([], Self::map_history_entry).optional()?;
        Ok(entry)
    }

    pub async fn toggle_saved_status(&self, id: i64) -> Result<()> {
        let conn = self.get_connection()?;

        // Get current saved status
        let current_saved: bool = conn.query_row(
            "SELECT saved FROM transcription_history WHERE id = ?1",
            params![id],
            |row| row.get("saved"),
        )?;

        let new_saved = !current_saved;

        conn.execute(
            "UPDATE transcription_history SET saved = ?1 WHERE id = ?2",
            params![new_saved, id],
        )?;

        debug!("Toggled saved status for entry {}: {}", id, new_saved);

        // Emit history updated event
        if let Err(e) = (HistoryUpdatePayload::Toggled { id }).emit(&self.app_handle) {
            error!("Failed to emit history-updated event: {}", e);
        }

        Ok(())
    }

    pub fn get_audio_file_path(&self, file_name: &str) -> PathBuf {
        self.recordings_dir.join(file_name)
    }

    fn get_entry_by_id_with_conn(conn: &Connection, id: i64) -> Result<Option<HistoryEntry>> {
        let mut stmt = conn.prepare(
            "SELECT
                id,
                file_name,
                timestamp,
                saved,
                title,
                transcription_text,
                post_processed_text,
                post_process_prompt,
                post_process_model,
                post_process_requested,
                audio_available
             FROM transcription_history
             WHERE id = ?1",
        )?;

        Ok(stmt.query_row([id], Self::map_history_entry).optional()?)
    }

    pub async fn get_entry_by_id(&self, id: i64) -> Result<Option<HistoryEntry>> {
        let conn = self.get_connection()?;
        Self::get_entry_by_id_with_conn(&conn, id)
    }

    pub async fn delete_entry(&self, id: i64) -> Result<()> {
        let conn = self.get_connection()?;

        if let Some(entry) = Self::get_entry_by_id_with_conn(&conn, id)? {
            let file_path = self.get_audio_file_path(&entry.file_name);
            match fs::remove_file(&file_path) {
                Ok(()) => debug!("Deleted audio file: {}", entry.file_name),
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(error) => {
                    error!("Failed to delete audio file {}: {}", entry.file_name, error);
                    return Err(error.into());
                }
            }
        }

        // Never unlink the row while a WAV removal is known to have failed.
        conn.execute(
            "DELETE FROM transcription_history WHERE id = ?1",
            params![id],
        )?;

        debug!("Deleted history entry with id: {}", id);

        if let Err(error) = (HistoryUpdatePayload::Deleted { id }).emit(&self.app_handle) {
            error!("Failed to emit history-updated event: {}", error);
        }

        Ok(())
    }

    fn format_timestamp_title(&self, timestamp: i64) -> String {
        if let Some(utc_datetime) = DateTime::from_timestamp(timestamp, 0) {
            // Convert UTC to local timezone
            let local_datetime = utc_datetime.with_timezone(&Local);
            local_datetime.format("%B %e, %Y - %l:%M%p").to_string()
        } else {
            format!("Recording {}", timestamp)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::{params, Connection};
    use tempfile::TempDir;

    fn setup_conn() -> Connection {
        let conn = Connection::open_in_memory().expect("open in-memory db");
        conn.execute_batch(
            "CREATE TABLE transcription_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_name TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                saved BOOLEAN NOT NULL DEFAULT 0,
                title TEXT NOT NULL,
                transcription_text TEXT NOT NULL,
                post_processed_text TEXT,
                post_process_prompt TEXT,
                post_process_requested BOOLEAN NOT NULL DEFAULT 0,
                post_process_model TEXT,
                audio_available BOOLEAN NOT NULL DEFAULT 1
            );",
        )
        .expect("create transcription_history table");
        conn
    }

    #[allow(clippy::too_many_arguments)]
    fn insert_entry(
        conn: &Connection,
        recordings_dir: Option<&Path>,
        timestamp: i64,
        text: &str,
        post_processed: Option<&str>,
        prompt: Option<&str>,
        model: Option<&str>,
        post_process_requested: bool,
    ) -> i64 {
        let file_name = format!("synthetic-{timestamp}.wav");
        conn.execute(
            "INSERT INTO transcription_history (
                file_name,
                timestamp,
                saved,
                title,
                transcription_text,
                post_processed_text,
                post_process_prompt,
                post_process_model,
                post_process_requested,
                audio_available
            ) VALUES (?1, ?2, 0, ?3, ?4, ?5, ?6, ?7, ?8, 1)",
            params![
                &file_name,
                timestamp,
                format!("Synthetic recording {timestamp}"),
                text,
                post_processed,
                prompt,
                model,
                post_process_requested,
            ],
        )
        .expect("insert synthetic history entry");

        if let Some(recordings_dir) = recordings_dir {
            fs::write(
                recordings_dir.join(file_name),
                b"synthetic audio placeholder",
            )
            .expect("write synthetic audio placeholder");
        }

        conn.last_insert_rowid()
    }

    fn insert_pair(conn: &Connection, recordings_dir: Option<&Path>, timestamp: i64) -> i64 {
        insert_entry(
            conn,
            recordings_dir,
            timestamp,
            &format!("synthetic raw {timestamp}"),
            Some(&format!("synthetic processed {timestamp}")),
            Some("synthetic exact prompt"),
            Some("synthetic-provider/synthetic-model"),
            true,
        )
    }

    fn latest_successful_pairs(conn: &Connection, limit: usize) -> Vec<HistoryEntry> {
        let mut stmt = conn
            .prepare(
                "SELECT
                    id,
                    file_name,
                    timestamp,
                    saved,
                    title,
                    transcription_text,
                    post_processed_text,
                    post_process_prompt,
                    post_process_model,
                    post_process_requested,
                    audio_available
                 FROM transcription_history
                 WHERE transcription_text != ''
                   AND COALESCE(post_processed_text, '') != ''
                   AND COALESCE(post_process_prompt, '') != ''
                 ORDER BY timestamp DESC, id DESC
                 LIMIT ?1",
            )
            .expect("prepare latest-pairs query");
        let entries = stmt
            .query_map(params![limit as i64], HistoryManager::map_history_entry)
            .expect("query latest pairs")
            .collect::<std::result::Result<Vec<_>, _>>()
            .expect("map latest pairs");
        entries
    }

    fn create_version_four_database(db_path: &Path) {
        let conn = Connection::open(db_path).expect("open old database");
        conn.execute_batch(
            "CREATE TABLE transcription_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_name TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                saved BOOLEAN NOT NULL DEFAULT 0,
                title TEXT NOT NULL,
                transcription_text TEXT NOT NULL,
                post_processed_text TEXT,
                post_process_prompt TEXT,
                post_process_requested BOOLEAN NOT NULL DEFAULT 0
            );
            INSERT INTO transcription_history (
                file_name, timestamp, title, transcription_text,
                post_processed_text, post_process_prompt, post_process_requested
            ) VALUES (
                'synthetic-present.wav', 100, 'Synthetic present recording',
                'synthetic present raw', 'synthetic present processed',
                'synthetic present prompt', 1
            );
            INSERT INTO transcription_history (
                file_name, timestamp, title, transcription_text,
                post_processed_text, post_process_prompt, post_process_requested
            ) VALUES (
                'synthetic-absent.wav', 200, 'Synthetic absent recording',
                'synthetic absent raw', 'synthetic absent processed',
                'synthetic absent prompt', 1
            );
            PRAGMA user_version = 4;",
        )
        .expect("create version-four database");
    }

    fn assert_migration_reconciles_after_restart(use_count_policy: bool) {
        let temp_dir = TempDir::new().expect("create temporary directory");
        let recordings_dir = temp_dir.path().join("recordings");
        fs::create_dir(&recordings_dir).expect("create recordings directory");
        fs::write(
            recordings_dir.join("synthetic-present.wav"),
            b"synthetic historical audio",
        )
        .expect("write present historical audio");
        let db_path = temp_dir.path().join("history.db");
        create_version_four_database(&db_path);

        let mut conn = Connection::open(&db_path).expect("open old database for migration");
        Migrations::new(MIGRATIONS.to_vec())
            .to_latest(&mut conn)
            .expect("migrate old database");
        HistoryManager::reconcile_audio_availability_with_conn(&conn, &recordings_dir)
            .expect("reconcile historical audio");

        let mut report = CleanupReport::default();
        if use_count_policy {
            HistoryManager::cleanup_audio_by_count_with_conn(
                &conn,
                &recordings_dir,
                5,
                &mut report,
            )
            .expect("apply count audio policy");
        }
        HistoryManager::cleanup_text_rows_with_conn(&conn, 1_000, &mut report)
            .expect("apply text policy");
        let version: i32 = conn
            .pragma_query_value(None, "user_version", |row| row.get(0))
            .expect("read migrated version");
        assert_eq!(version, MIGRATIONS.len() as i32);
        drop(conn);

        let conn = Connection::open(&db_path).expect("restart migrated database");
        let present = HistoryManager::get_entry_by_id_with_conn(&conn, 1)
            .expect("read present UI row")
            .expect("present UI row retained");
        let absent = HistoryManager::get_entry_by_id_with_conn(&conn, 2)
            .expect("read absent UI row")
            .expect("absent UI row retained");
        assert!(present.audio_available);
        assert!(!absent.audio_available);
        assert_eq!(present.post_process_model, None);
        assert_eq!(absent.post_process_model, None);
        assert_eq!(latest_successful_pairs(&conn, 10).len(), 2);
        assert!(recordings_dir.join(&present.file_name).is_file());
    }

    fn assert_purged_retained_audio(
        conn: &Connection,
        recordings_dir: &Path,
        id: i64,
        saved: bool,
    ) {
        let entry = HistoryManager::get_entry_by_id_with_conn(conn, id)
            .expect("read purged retained-audio row")
            .expect("purged retained-audio row exists");
        assert_eq!(entry.timestamp, 1);
        assert_eq!(entry.saved, saved);
        assert_eq!(entry.title, "Synthetic recording 1");
        assert_eq!(entry.transcription_text, "");
        assert_eq!(entry.post_processed_text, None);
        assert_eq!(entry.post_process_prompt, None);
        assert_eq!(entry.post_process_model, None);
        assert!(!entry.post_process_requested);
        assert!(entry.audio_available);
        assert!(recordings_dir.join(&entry.file_name).is_file());
        assert_eq!(latest_successful_pairs(conn, 2_000).len(), 1_000);
    }

    #[test]
    fn migration_reconciles_present_and_absent_audio_under_never_after_restart() {
        assert_migration_reconciles_after_restart(false);
    }

    #[test]
    fn migration_reconciles_present_and_absent_audio_under_count_after_restart() {
        assert_migration_reconciles_after_restart(true);
    }

    #[test]
    fn regular_wav_check_rejects_missing_paths_and_directories() {
        let temp_dir = TempDir::new().expect("create recordings directory");
        let recordings_dir = temp_dir.path();
        fs::write(
            recordings_dir.join("synthetic-present.wav"),
            b"synthetic audio",
        )
        .expect("write regular WAV placeholder");
        fs::create_dir(recordings_dir.join("synthetic-directory.wav"))
            .expect("create WAV-named directory");

        assert!(HistoryManager::is_regular_wav_file(
            recordings_dir,
            "synthetic-present.wav"
        ));
        assert!(!HistoryManager::is_regular_wav_file(
            recordings_dir,
            "synthetic-missing.wav"
        ));
        assert!(!HistoryManager::is_regular_wav_file(
            recordings_dir,
            "synthetic-directory.wav"
        ));
        assert!(!HistoryManager::is_regular_wav_file(
            recordings_dir,
            "../synthetic-present.wav"
        ));
    }

    #[test]
    fn orphan_cleanup_removes_only_untracked_regular_wav_files() {
        let conn = setup_conn();
        let temp_dir = TempDir::new().expect("create recordings directory");
        let recordings_dir = temp_dir.path();
        let tracked_id = insert_pair(&conn, Some(recordings_dir), 1);
        let tracked_file = HistoryManager::get_entry_by_id_with_conn(&conn, tracked_id)
            .expect("read tracked row")
            .expect("tracked row exists")
            .file_name;
        let orphan = recordings_dir.join("synthetic-orphan.WAV");
        let non_wav = recordings_dir.join("synthetic-note.txt");
        let wav_directory = recordings_dir.join("synthetic-directory.wav");
        fs::write(&orphan, b"synthetic orphan audio").expect("write orphan WAV");
        fs::write(&non_wav, b"synthetic note").expect("write non-WAV file");
        fs::create_dir(&wav_directory).expect("create WAV-named directory");

        let wav_symlink = {
            let wav_symlink = recordings_dir.join("synthetic-symlink.wav");
            std::os::unix::fs::symlink(&orphan, &wav_symlink).expect("create WAV symlink");
            wav_symlink
        };

        let pending_audio_files = Mutex::new(HashSet::new());
        HistoryManager::cleanup_orphaned_wav_files_with_conn(
            &conn,
            recordings_dir,
            &pending_audio_files,
        )
        .expect("clean orphaned WAV files");

        assert!(recordings_dir.join(tracked_file).is_file());
        assert!(!orphan.exists());
        assert!(non_wav.is_file());
        assert!(wav_directory.is_dir());
        assert!(wav_symlink.is_symlink());
    }

    #[test]
    fn exclusive_audio_reservation_skips_owned_collision_without_mutation() {
        let temp_dir = TempDir::new().expect("create recordings directory");
        let pending_audio_files = Mutex::new(HashSet::new());
        let owned_path = temp_dir.path().join("synthetic-owned.wav");
        fs::write(&owned_path, b"history-owned audio").expect("write owned fixture");

        let mut guard = reserve_pending_audio_from_candidates(
            temp_dir.path(),
            &pending_audio_files,
            [
                "synthetic-owned.wav".to_string(),
                "synthetic-reserved.wav".to_string(),
            ],
        )
        .expect("skip collision and reserve a new inode");
        let reserved_path = guard.path().to_path_buf();
        crate::audio_toolkit::write_wav_file(
            guard.take_writer().expect("take exact reservation handle"),
            &[0.0, 0.25, -0.25],
        )
        .expect("write only through exclusive reservation");

        assert_eq!(fs::read(&owned_path).unwrap(), b"history-owned audio");
        assert_eq!(guard.path(), temp_dir.path().join("synthetic-reserved.wav"));
        assert_eq!(
            crate::audio_toolkit::read_wav_samples(&reserved_path)
                .unwrap()
                .len(),
            3
        );
        assert_eq!(pending_audio_files.lock().unwrap().len(), 1);
        drop(guard);
        assert_eq!(fs::read(&owned_path).unwrap(), b"history-owned audio");
        assert!(!temp_dir.path().join("synthetic-reserved.wav").exists());
        assert!(pending_audio_files.lock().unwrap().is_empty());
    }

    #[test]
    fn repeated_candidates_reserve_distinct_mode_safe_files() {
        use std::os::unix::fs::PermissionsExt;

        let temp_dir = TempDir::new().expect("create recordings directory");
        let pending_audio_files = Mutex::new(HashSet::new());
        let candidates = || {
            [
                wav_candidate_name(1_723_456_789_012, 42, 0),
                wav_candidate_name(1_723_456_789_012, 42, 1),
            ]
        };
        let first = reserve_pending_audio_from_candidates(
            temp_dir.path(),
            &pending_audio_files,
            candidates(),
        )
        .expect("reserve first same-time request");
        let second = reserve_pending_audio_from_candidates(
            temp_dir.path(),
            &pending_audio_files,
            candidates(),
        )
        .expect("collision authority reserves second same-time request");

        assert_ne!(first.path(), second.path());
        for path in [first.path(), second.path()] {
            let mode = fs::metadata(path).unwrap().permissions().mode() & 0o777;
            assert_eq!(mode, 0o600, "reserved WAV must be owner-only");
        }
    }

    #[test]
    fn uncommitted_reserved_audio_is_removed_immediately() {
        let temp_dir = TempDir::new().expect("create recordings directory");
        let pending_audio_files = Mutex::new(HashSet::new());
        let guard = reserve_pending_audio_from_candidates(
            temp_dir.path(),
            &pending_audio_files,
            ["synthetic-cancelled.wav".to_string()],
        )
        .expect("reserve cancelled request WAV");
        let path = guard.path().to_path_buf();
        drop(guard);

        assert!(!path.exists());
        assert!(pending_audio_files.lock().unwrap().is_empty());
    }

    #[test]
    fn inserted_history_owned_audio_survives_guard_drop() {
        let conn = setup_conn();
        let temp_dir = TempDir::new().expect("create recordings directory");
        let pending_audio_files = Mutex::new(HashSet::new());
        let mut guard = reserve_pending_audio_from_candidates(
            temp_dir.path(),
            &pending_audio_files,
            ["synthetic-tracked.wav".to_string()],
        )
        .expect("reserve tracked request WAV");
        let path = guard.path().to_path_buf();
        let mut writer = guard.take_writer().expect("take exact reserved writer");
        use std::io::Write;
        writer.write_all(b"synthetic tracked audio").unwrap();
        drop(writer);

        conn.execute(
            "INSERT INTO transcription_history (
                file_name, timestamp, saved, title, transcription_text,
                post_processed_text, post_process_prompt, post_process_model,
                post_process_requested, audio_available
             ) VALUES ('synthetic-tracked.wav', 1, 0, 'Synthetic tracked',
                       'raw', 'processed', 'prompt', 'provider/model', 1, 1)",
            [],
        )
        .expect("insert history owner");
        // This is the same immediate transfer invoked after production INSERT
        // and before policy cleanup in save_entry_with_ownership.
        guard.mark_history_owned();
        drop(guard);

        assert!(path.is_file());
        assert!(pending_audio_files.lock().unwrap().is_empty());
        let mut report = CleanupReport::default();
        HistoryManager::cleanup_audio_by_count_with_conn(&conn, temp_dir.path(), 1, &mut report)
            .expect("apply authoritative retention policy");
        assert!(path.is_file());
    }

    #[test]
    fn pending_audio_is_ignored_until_its_history_row_exists() {
        let conn = setup_conn();
        let temp_dir = TempDir::new().expect("create recordings directory");
        let recordings_dir = temp_dir.path();
        let file_name = "synthetic-pending.wav";
        let pending_file = recordings_dir.join(file_name);
        let pending_audio_files = Mutex::new(HashSet::from([OsString::from(file_name)]));
        fs::write(&pending_file, b"synthetic pending audio").expect("publish pending WAV");

        HistoryManager::cleanup_orphaned_wav_files_with_conn(
            &conn,
            recordings_dir,
            &pending_audio_files,
        )
        .expect("skip pending WAV");
        assert!(pending_file.is_file());

        conn.execute(
            "INSERT INTO transcription_history (
                file_name, timestamp, saved, title, transcription_text,
                post_processed_text, post_process_prompt, post_process_model,
                post_process_requested, audio_available
             ) VALUES (?1, 1, 0, 'Synthetic pending recording',
                       'synthetic pending raw', 'synthetic pending processed',
                       'synthetic pending prompt', 'synthetic-provider/synthetic-model', 1, 1)",
            params![file_name],
        )
        .expect("commit pending history row");
        pending_audio_files
            .lock()
            .expect("lock pending registry")
            .clear();

        HistoryManager::cleanup_orphaned_wav_files_with_conn(
            &conn,
            recordings_dir,
            &pending_audio_files,
        )
        .expect("retain newly tracked WAV after pending release");
        assert!(pending_file.is_file());
    }

    #[test]
    fn orphan_cleanup_reports_removal_failure_and_retries() {
        use std::os::unix::fs::PermissionsExt;

        let conn = setup_conn();
        let temp_dir = TempDir::new().expect("create recordings directory");
        let recordings_dir = temp_dir.path();
        let orphan = recordings_dir.join("synthetic-blocked-orphan.wav");
        fs::write(&orphan, b"synthetic blocked orphan audio").expect("write orphan WAV");

        fs::set_permissions(recordings_dir, fs::Permissions::from_mode(0o555))
            .expect("block orphan removal");
        let pending_audio_files = Mutex::new(HashSet::new());
        let cleanup_result = HistoryManager::cleanup_orphaned_wav_files_with_conn(
            &conn,
            recordings_dir,
            &pending_audio_files,
        );
        fs::set_permissions(recordings_dir, fs::Permissions::from_mode(0o755))
            .expect("restore recordings permissions");

        let cleanup_error = cleanup_result.expect_err("surface orphan removal failure");
        assert!(cleanup_error
            .downcast_ref::<CleanupFilesystemError>()
            .is_some());
        assert!(orphan.is_file());

        HistoryManager::cleanup_orphaned_wav_files_with_conn(
            &conn,
            recordings_dir,
            &pending_audio_files,
        )
        .expect("retry orphan removal");
        assert!(!orphan.exists());
    }

    #[test]
    fn exact_pair_metadata_and_latest_query_survive_reopen() {
        let temp_dir = TempDir::new().expect("create temporary directory");
        let db_path = temp_dir.path().join("history.db");
        let mut conn = Connection::open(&db_path).expect("open temporary database");
        Migrations::new(MIGRATIONS.to_vec())
            .to_latest(&mut conn)
            .expect("initialize temporary database");
        insert_pair(&conn, None, 100);
        insert_entry(
            &conn,
            None,
            200,
            "synthetic raw-only row",
            None,
            None,
            None,
            false,
        );
        drop(conn);

        let conn = Connection::open(&db_path).expect("reopen temporary database");
        let entries = latest_successful_pairs(&conn, 1);
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].timestamp, 100);
        assert_eq!(
            entries[0].post_process_prompt.as_deref(),
            Some("synthetic exact prompt")
        );
        assert_eq!(
            entries[0].post_process_model.as_deref(),
            Some("synthetic-provider/synthetic-model")
        );
    }

    #[test]
    fn count_five_deletes_audio_less_pair_one_at_pair_one_thousand_one() {
        let conn = setup_conn();
        let temp_dir = TempDir::new().expect("create recordings directory");
        let recordings_dir = temp_dir.path();

        let first_id = insert_pair(&conn, Some(recordings_dir), 1);
        for timestamp in 2..=6 {
            insert_pair(&conn, Some(recordings_dir), timestamp);
        }
        let mut report = CleanupReport::default();
        HistoryManager::cleanup_audio_by_count_with_conn(&conn, recordings_dir, 5, &mut report)
            .expect("apply audio limit at pair six");
        HistoryManager::cleanup_text_rows_with_conn(&conn, 1_000, &mut report)
            .expect("apply text limit at pair six");
        assert_eq!(
            conn.query_row("SELECT COUNT(*) FROM transcription_history", [], |row| {
                row.get::<_, i64>(0)
            })
            .expect("count pair-six rows"),
            6
        );
        assert!(
            !HistoryManager::get_entry_by_id_with_conn(&conn, first_id)
                .expect("read first pair")
                .expect("first pair retained at pair six")
                .audio_available
        );

        for timestamp in 7..=1_001 {
            insert_pair(&conn, Some(recordings_dir), timestamp);
        }
        let mut report = CleanupReport::default();
        HistoryManager::cleanup_audio_by_count_with_conn(&conn, recordings_dir, 5, &mut report)
            .expect("apply final audio limit");
        HistoryManager::cleanup_text_rows_with_conn(&conn, 1_000, &mut report)
            .expect("apply final text limit");

        assert!(HistoryManager::get_entry_by_id_with_conn(&conn, first_id)
            .expect("read expired first pair")
            .is_none());
        assert!(report.entries_deleted.contains(&first_id));
        assert_eq!(latest_successful_pairs(&conn, 2_000).len(), 1_000);
        assert_eq!(
            conn.query_row(
                "SELECT COUNT(*) FROM transcription_history WHERE audio_available = 1",
                [],
                |row| row.get::<_, i64>(0),
            )
            .expect("count retained audio"),
            5
        );
        assert_eq!(
            fs::read_dir(recordings_dir)
                .expect("read recordings directory")
                .count(),
            5
        );
    }

    #[test]
    fn never_policy_purges_pair_one_thousand_one_text_but_retains_oldest_audio_row() {
        let conn = setup_conn();
        let temp_dir = TempDir::new().expect("create recordings directory");
        let recordings_dir = temp_dir.path();

        let first_id = insert_pair(&conn, Some(recordings_dir), 1);
        conn.execute(
            "UPDATE transcription_history SET saved = 1 WHERE id = ?1",
            params![first_id],
        )
        .expect("mark oldest row saved");
        for timestamp in 2..=1_001 {
            insert_pair(&conn, Some(recordings_dir), timestamp);
        }

        let mut report = CleanupReport::default();
        HistoryManager::cleanup_text_rows_with_conn(&conn, 1_000, &mut report)
            .expect("apply text cleanup under Never audio policy");

        assert!(report.entries_updated.contains(&first_id));
        assert!(report.entries_deleted.is_empty());
        assert_purged_retained_audio(&conn, recordings_dir, first_id, true);
        assert_eq!(
            fs::read_dir(recordings_dir)
                .expect("read Never recordings")
                .count(),
            1_001
        );
    }

    #[test]
    fn time_policy_purges_pair_one_thousand_one_text_but_retains_recent_oldest_audio_row() {
        let conn = setup_conn();
        let temp_dir = TempDir::new().expect("create recordings directory");
        let recordings_dir = temp_dir.path();

        let first_id = insert_pair(&conn, Some(recordings_dir), 1);
        for timestamp in 2..=1_001 {
            insert_pair(&conn, Some(recordings_dir), timestamp);
        }

        let mut report = CleanupReport::default();
        HistoryManager::cleanup_audio_by_time_with_conn(&conn, recordings_dir, 0, &mut report)
            .expect("retain all recent time-policy audio");
        HistoryManager::cleanup_text_rows_with_conn(&conn, 1_000, &mut report)
            .expect("apply text cleanup under time audio policy");

        assert!(report.entries_updated.contains(&first_id));
        assert!(report.entries_deleted.is_empty());
        assert_purged_retained_audio(&conn, recordings_dir, first_id, false);
    }

    #[test]
    fn saved_old_row_keeps_text_but_not_audio_outside_latest_five() {
        let conn = setup_conn();
        let temp_dir = TempDir::new().expect("create recordings directory");
        let recordings_dir = temp_dir.path();

        let saved_id = insert_pair(&conn, Some(recordings_dir), 1);
        conn.execute(
            "UPDATE transcription_history SET saved = 1 WHERE id = ?1",
            params![saved_id],
        )
        .expect("mark oldest synthetic row saved");
        for timestamp in 2..=6 {
            insert_pair(&conn, Some(recordings_dir), timestamp);
        }

        let mut report = CleanupReport::default();
        HistoryManager::cleanup_audio_by_count_with_conn(&conn, recordings_dir, 5, &mut report)
            .expect("apply strict audio limit");
        HistoryManager::cleanup_text_rows_with_conn(&conn, 1_000, &mut report)
            .expect("retain saved text row under text limit");

        let saved_entry = HistoryManager::get_entry_by_id_with_conn(&conn, saved_id)
            .expect("read saved row")
            .expect("saved text row retained");
        assert!(saved_entry.saved);
        assert!(!saved_entry.audio_available);
        assert!(!recordings_dir.join(&saved_entry.file_name).exists());
    }

    #[test]
    fn count_audio_removal_failure_returns_error_keeps_truth_and_retries() {
        let conn = setup_conn();
        let temp_dir = TempDir::new().expect("create recordings directory");
        let recordings_dir = temp_dir.path();

        let oldest_id = insert_pair(&conn, None, 1);
        let blocked_path = recordings_dir.join("synthetic-1.wav");
        fs::create_dir(&blocked_path).expect("create non-removable file-path directory");
        let second_id = insert_pair(&conn, Some(recordings_dir), 2);
        for timestamp in 3..=7 {
            insert_pair(&conn, Some(recordings_dir), timestamp);
        }

        let mut report = CleanupReport::default();
        let cleanup_error =
            HistoryManager::cleanup_audio_by_count_with_conn(&conn, recordings_dir, 5, &mut report)
                .expect_err("surface synthetic count removal failure");
        assert!(cleanup_error
            .downcast_ref::<CleanupFilesystemError>()
            .is_some());
        assert!(
            HistoryManager::get_entry_by_id_with_conn(&conn, oldest_id)
                .expect("read blocked audio row")
                .expect("blocked audio row retained")
                .audio_available
        );
        assert!(
            !HistoryManager::get_entry_by_id_with_conn(&conn, second_id)
                .expect("read best-effort cleaned row")
                .expect("best-effort cleaned row retained")
                .audio_available
        );
        assert_eq!(report.entries_updated, vec![second_id]);

        fs::remove_dir(&blocked_path).expect("remove blocking directory");
        fs::write(&blocked_path, b"synthetic audio placeholder")
            .expect("replace blocking directory with audio file");
        let mut retry_report = CleanupReport::default();
        HistoryManager::cleanup_audio_by_count_with_conn(
            &conn,
            recordings_dir,
            5,
            &mut retry_report,
        )
        .expect("retry synthetic count audio removal");
        assert!(
            !HistoryManager::get_entry_by_id_with_conn(&conn, oldest_id)
                .expect("read retried audio row")
                .expect("retried audio text row retained")
                .audio_available
        );
        assert_eq!(retry_report.entries_updated, vec![oldest_id]);
    }

    #[test]
    fn time_audio_removal_failure_returns_error_keeps_truth_and_retries() {
        let conn = setup_conn();
        let temp_dir = TempDir::new().expect("create recordings directory");
        let recordings_dir = temp_dir.path();

        let blocked_id = insert_pair(&conn, None, 1);
        let blocked_path = recordings_dir.join("synthetic-1.wav");
        fs::create_dir(&blocked_path).expect("create non-removable file-path directory");
        let removable_id = insert_pair(&conn, Some(recordings_dir), 2);

        let mut report = CleanupReport::default();
        let cleanup_error =
            HistoryManager::cleanup_audio_by_time_with_conn(&conn, recordings_dir, 3, &mut report)
                .expect_err("surface synthetic time removal failure");
        assert!(cleanup_error
            .downcast_ref::<CleanupFilesystemError>()
            .is_some());
        assert!(
            HistoryManager::get_entry_by_id_with_conn(&conn, blocked_id)
                .expect("read blocked time-policy row")
                .expect("blocked time-policy row retained")
                .audio_available
        );
        assert!(
            !HistoryManager::get_entry_by_id_with_conn(&conn, removable_id)
                .expect("read removable time-policy row")
                .expect("removable time-policy row retained")
                .audio_available
        );

        fs::remove_dir(&blocked_path).expect("remove blocking directory");
        fs::write(&blocked_path, b"synthetic audio placeholder")
            .expect("replace blocking directory with audio file");
        let mut retry_report = CleanupReport::default();
        HistoryManager::cleanup_audio_by_time_with_conn(
            &conn,
            recordings_dir,
            3,
            &mut retry_report,
        )
        .expect("retry synthetic time audio removal");
        assert!(
            !HistoryManager::get_entry_by_id_with_conn(&conn, blocked_id)
                .expect("read retried time-policy row")
                .expect("retried time-policy row retained")
                .audio_available
        );
        assert_eq!(retry_report.entries_updated, vec![blocked_id]);
    }

    #[test]
    fn incomplete_rows_are_removed_only_after_their_audio_expires() {
        let conn = setup_conn();
        let temp_dir = TempDir::new().expect("create recordings directory");
        let recordings_dir = temp_dir.path();

        let retained_pair = insert_pair(&conn, Some(recordings_dir), 1);
        let failed = insert_entry(&conn, Some(recordings_dir), 2, "", None, None, None, true);
        let raw_only = insert_entry(
            &conn,
            Some(recordings_dir),
            3,
            "synthetic raw only",
            None,
            None,
            None,
            false,
        );
        for timestamp in 4..=8 {
            insert_pair(&conn, Some(recordings_dir), timestamp);
        }

        let mut before_audio_report = CleanupReport::default();
        HistoryManager::cleanup_text_rows_with_conn(&conn, 1_000, &mut before_audio_report)
            .expect("retain incomplete rows while audio remains");
        assert!(HistoryManager::get_entry_by_id_with_conn(&conn, failed)
            .expect("read pre-expiry failed row")
            .is_some());
        assert!(HistoryManager::get_entry_by_id_with_conn(&conn, raw_only)
            .expect("read pre-expiry raw-only row")
            .is_some());

        let mut report = CleanupReport::default();
        HistoryManager::cleanup_audio_by_count_with_conn(&conn, recordings_dir, 5, &mut report)
            .expect("apply audio cleanup");
        HistoryManager::cleanup_text_rows_with_conn(&conn, 1_000, &mut report)
            .expect("apply incomplete-row cleanup");

        assert!(
            HistoryManager::get_entry_by_id_with_conn(&conn, retained_pair)
                .expect("read retained pair")
                .is_some()
        );
        assert!(HistoryManager::get_entry_by_id_with_conn(&conn, failed)
            .expect("read failed row")
            .is_none());
        assert!(HistoryManager::get_entry_by_id_with_conn(&conn, raw_only)
            .expect("read raw-only row")
            .is_none());
    }

    #[test]
    fn get_latest_entry_returns_none_when_empty() {
        let conn = setup_conn();
        let entry = HistoryManager::get_latest_entry_with_conn(&conn).expect("fetch latest entry");
        assert!(entry.is_none());
    }

    #[test]
    fn get_latest_entry_returns_newest_entry() {
        let conn = setup_conn();
        insert_entry(&conn, None, 100, "first", None, None, None, false);
        insert_entry(
            &conn,
            None,
            200,
            "second",
            Some("processed"),
            None,
            None,
            false,
        );

        let entry = HistoryManager::get_latest_entry_with_conn(&conn)
            .expect("fetch latest entry")
            .expect("entry exists");
        assert_eq!(entry.timestamp, 200);
        assert_eq!(entry.transcription_text, "second");
        assert_eq!(entry.post_processed_text.as_deref(), Some("processed"));
    }

    #[test]
    fn get_latest_completed_entry_skips_purged_and_empty_entries() {
        let conn = setup_conn();
        insert_entry(
            &conn,
            None,
            100,
            "synthetic completed",
            None,
            None,
            None,
            false,
        );
        insert_entry(&conn, None, 200, "", None, None, None, false);

        let entry = HistoryManager::get_latest_completed_entry_with_conn(&conn)
            .expect("fetch latest completed entry")
            .expect("completed entry exists");

        assert_eq!(entry.timestamp, 100);
        assert_eq!(entry.transcription_text, "synthetic completed");
    }
}
