use crate::operation::StartTarget;
use clap::Parser;
use std::path::PathBuf;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum RunningInstanceCommand {
    StartOrStop { target: StartTarget },
    Cancel,
    Show,
}

#[derive(Parser, Debug, Clone, Default)]
#[command(name = "handy", about = "Handy - Speech to Text")]
pub struct CliArgs {
    /// Start with the main window hidden
    #[arg(long)]
    pub start_hidden: bool,

    /// Disable the system tray icon
    #[arg(long)]
    pub no_tray: bool,

    /// Start or stop transcription in an already-running instance
    #[arg(long, conflicts_with = "cancel")]
    pub toggle_transcription: bool,

    /// Exact public Herdr pane id for a --toggle-transcription start
    #[arg(
        long,
        value_name = "PANE_ID",
        requires = "toggle_transcription",
        conflicts_with = "cancel"
    )]
    pub herdr_pane: Option<String>,

    /// Cancel workstation-local recording or processing in an already-running instance
    #[arg(long, conflicts_with = "toggle_transcription")]
    pub cancel: bool,

    /// Enable debug mode with verbose logging
    #[arg(long)]
    pub debug: bool,

    /// Transcribe this WAV (16 kHz mono) headlessly and exit. Runs the same
    /// batch transcription path as the app — no mic, no VAD, no download
    /// (the model must already be installed).
    #[arg(short = 'f', long, value_name = "WAV")]
    pub transcribe_file: Option<PathBuf>,

    /// Model id to load for --transcribe-file (default: the selected model).
    #[arg(long)]
    pub model: Option<String>,

    /// Hard-select the compute device for --transcribe-file by its registry
    /// index (see --list-devices). Omit to use the persisted accelerator
    /// setting. transcribe-cpp (whisper-family) models only.
    #[arg(long, value_name = "N")]
    pub device_index: Option<usize>,

    /// List the transcribe-cpp compute devices (with indices) and exit.
    #[arg(long)]
    pub list_devices: bool,

    /// List the available models (with ids) and exit. Pass an id to --model.
    /// Honors --json for machine-readable output.
    #[arg(long)]
    pub list_models: bool,

    /// Repeat the transcription N times (best_ms reports the fastest run).
    #[arg(long, value_name = "N")]
    pub repeat: Option<usize>,

    /// Emit --transcribe-file results as JSON.
    #[arg(long)]
    pub json: bool,
}

/// Classify arguments forwarded by the single-instance plugin. Clap performs
/// the public flag/value pairing checks before a secondary process can forward
/// them; parsing again keeps this process boundary fail-closed as well.
pub(crate) fn running_instance_command(args: &[String]) -> Result<RunningInstanceCommand, String> {
    let parsed = CliArgs::try_parse_from(args).map_err(|error| error.to_string())?;
    if parsed.toggle_transcription {
        let target = parsed
            .herdr_pane
            .map(StartTarget::ExplicitPane)
            .unwrap_or(StartTarget::Auto);
        Ok(RunningInstanceCommand::StartOrStop { target })
    } else if parsed.cancel {
        Ok(RunningInstanceCommand::Cancel)
    } else {
        Ok(RunningInstanceCommand::Show)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn command(args: &[&str]) -> Result<RunningInstanceCommand, String> {
        running_instance_command(
            &args
                .iter()
                .map(|argument| argument.to_string())
                .collect::<Vec<_>>(),
        )
    }

    #[test]
    fn classifies_legacy_and_explicit_start_or_stop_controls() {
        assert_eq!(
            command(&["handy", "--toggle-transcription"]),
            Ok(RunningInstanceCommand::StartOrStop {
                target: StartTarget::Auto,
            })
        );
        assert_eq!(
            command(&["handy", "--toggle-transcription", "--herdr-pane", "w2H:p13",]),
            Ok(RunningInstanceCommand::StartOrStop {
                target: StartTarget::ExplicitPane("w2H:p13".to_string()),
            })
        );
    }

    #[test]
    fn forwards_pane_text_for_lifecycle_aware_start_validation() {
        assert_eq!(
            command(&[
                "handy",
                "--toggle-transcription",
                "--herdr-pane",
                "malformed",
            ]),
            Ok(RunningInstanceCommand::StartOrStop {
                target: StartTarget::ExplicitPane("malformed".to_string()),
            })
        );
    }

    #[test]
    fn rejects_missing_duplicate_or_orphaned_pane_arguments() {
        for args in [
            vec!["handy", "--herdr-pane", "w2H:p13"],
            vec!["handy", "--toggle-transcription", "--herdr-pane"],
            vec![
                "handy",
                "--toggle-transcription",
                "--herdr-pane",
                "w2H:p13",
                "--herdr-pane",
                "w2H:p14",
            ],
            vec!["handy", "--toggle-transcription", "w2H:p13"],
        ] {
            assert!(
                command(&args).is_err(),
                "accepted malformed pairing {args:?}"
            );
        }
    }

    #[test]
    fn cancel_is_targetless_and_separate_from_start_or_stop() {
        assert_eq!(
            command(&["handy", "--cancel"]),
            Ok(RunningInstanceCommand::Cancel)
        );
        for args in [
            vec!["handy", "--cancel", "--toggle-transcription"],
            vec!["handy", "--cancel", "--herdr-pane", "w2H:p13"],
        ] {
            assert!(
                command(&args).is_err(),
                "accepted conflicting control {args:?}"
            );
        }
    }

    #[test]
    fn non_control_invocation_shows_the_running_instance() {
        assert_eq!(
            command(&["handy", "--start-hidden"]),
            Ok(RunningInstanceCommand::Show)
        );
    }
}
