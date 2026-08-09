pub mod audio;
pub mod history;
pub mod models;
pub mod transcription;

use crate::settings::{get_settings, write_settings, AppSettings, LogLevel};
use crate::utils::cancel_current_operation;
use tauri::{AppHandle, Manager};
use tauri_plugin_opener::OpenerExt;

#[tauri::command]
#[specta::specta]
pub fn cancel_operation(app: AppHandle) {
    cancel_current_operation(&app);
}

#[tauri::command]
#[specta::specta]
pub fn get_app_dir_path(app: AppHandle) -> Result<String, String> {
    let app_data_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("Failed to get app data directory: {error}"))?;
    Ok(app_data_dir.to_string_lossy().to_string())
}

#[tauri::command]
#[specta::specta]
pub fn get_app_settings(app: AppHandle) -> Result<AppSettings, String> {
    Ok(get_settings(&app))
}

#[tauri::command]
#[specta::specta]
pub fn get_default_settings() -> Result<AppSettings, String> {
    Ok(crate::settings::get_default_settings())
}

#[tauri::command]
#[specta::specta]
pub fn get_log_dir_path(app: AppHandle) -> Result<String, String> {
    let log_dir = app
        .path()
        .app_log_dir()
        .map_err(|error| format!("Failed to get log directory: {error}"))?;
    Ok(log_dir.to_string_lossy().to_string())
}

#[specta::specta]
#[tauri::command]
pub fn set_log_level(app: AppHandle, level: LogLevel) -> Result<(), String> {
    let tauri_log_level: tauri_plugin_log::LogLevel = level.into();
    let log_level: log::Level = tauri_log_level.into();
    crate::FILE_LOG_LEVEL.store(
        log_level.to_level_filter() as u8,
        std::sync::atomic::Ordering::Relaxed,
    );

    let mut settings = get_settings(&app);
    settings.log_level = level;
    write_settings(&app, settings);
    Ok(())
}

#[specta::specta]
#[tauri::command]
pub fn open_recordings_folder(app: AppHandle) -> Result<(), String> {
    let recordings_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("Failed to get app data directory: {error}"))?
        .join("recordings");
    app.opener()
        .open_path(recordings_dir.to_string_lossy().to_string(), None::<String>)
        .map_err(|error| format!("Failed to open recordings folder: {error}"))
}

#[specta::specta]
#[tauri::command]
pub fn open_log_dir(app: AppHandle) -> Result<(), String> {
    let log_dir = app
        .path()
        .app_log_dir()
        .map_err(|error| format!("Failed to get log directory: {error}"))?;
    app.opener()
        .open_path(log_dir.to_string_lossy().to_string(), None::<String>)
        .map_err(|error| format!("Failed to open log directory: {error}"))
}

#[specta::specta]
#[tauri::command]
pub fn open_app_data_dir(app: AppHandle) -> Result<(), String> {
    let app_data_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("Failed to get app data directory: {error}"))?;
    app.opener()
        .open_path(app_data_dir.to_string_lossy().to_string(), None::<String>)
        .map_err(|error| format!("Failed to open app data directory: {error}"))
}

/// Try to initialize the Linux keyboard/mouse simulation backend.
#[specta::specta]
#[tauri::command]
pub fn initialize_enigo(app: AppHandle) -> Result<(), String> {
    use crate::input::EnigoState;

    if app.try_state::<EnigoState>().is_some() {
        log::debug!("Enigo already initialized");
        return Ok(());
    }

    match EnigoState::new() {
        Ok(enigo_state) => {
            app.manage(enigo_state);
            log::info!("Enigo initialized successfully");
            Ok(())
        }
        Err(error) => {
            log::warn!("Failed to initialize Enigo: {error}");
            Err(format!("Failed to initialize input system: {error}"))
        }
    }
}

pub struct ShortcutsInitialized;

/// Initialize Linux keyboard shortcuts. This is idempotent.
#[specta::specta]
#[tauri::command]
pub fn initialize_shortcuts(app: AppHandle) -> Result<(), String> {
    if app.try_state::<ShortcutsInitialized>().is_some() {
        log::debug!("Shortcuts already initialized");
        return Ok(());
    }

    crate::shortcut::init_shortcuts(&app);
    app.manage(ShortcutsInitialized);
    log::info!("Shortcuts initialized successfully");
    Ok(())
}
