//! Conservative post-ASR cleanup using the 11M-parameter FDT Mini classifier.
//!
//! The published model labels the first WordPiece of every whitespace-delimited
//! word with KEEP, DELETE, KEEP_STRIP_COMMA, or KEEP_CAPITALIZE. The latter two
//! labels are structural repairs around a deletion run, so this module accepts
//! or rejects the complete edit transaction rather than thresholding words
//! independently.

use anyhow::{bail, ensure, Context, Result};
use log::{debug, info};
use ort::{
    session::{builder::GraphOptimizationLevel, Session},
    value::Tensor,
};
use sha2::{Digest, Sha256};
use std::{
    fs,
    panic::{catch_unwind, AssertUnwindSafe},
    path::{Path, PathBuf},
    sync::Mutex,
    time::{Duration, Instant},
};
use tokenizers::{Encoding, Tokenizer, TruncationDirection, TruncationParams, TruncationStrategy};

pub const MODEL_SUBDIR: &str = "text-cleanup/fdt-mini-11m";
pub const DEFAULT_SPAN_CONFIDENCE: f32 = 0.70;
const MAX_TOKENS: usize = 128;
const OVERLAP_TOKENS: usize = 32;
const LABEL_COUNT: usize = 4;

const MODEL_SHA256: &str = "277208ae7810af2c1b96e9972939ef2968e9fccd985c28ec2695aa472c54144f";
const TOKENIZER_SHA256: &str = "2fc687b11de0bc1b3d8348f92e3b49ef1089a621506c7661fbf3248fcd54947e";
const CONFIG_SHA256: &str = "5950a263d977482445208831688f2bd0c5bed390d94e98e3898199a8a29e1fe4";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Label {
    Keep,
    Delete,
    StripComma,
    Capitalize,
}

impl Label {
    fn from_index(index: usize) -> Result<Self> {
        match index {
            0 => Ok(Self::Keep),
            1 => Ok(Self::Delete),
            2 => Ok(Self::StripComma),
            3 => Ok(Self::Capitalize),
            _ => bail!("FDT returned unknown label index {index}"),
        }
    }
}

#[derive(Clone, Copy, Debug)]
struct WordPrediction {
    label: Label,
    confidence: f32,
    context: usize,
}

#[derive(Clone, Debug)]
pub struct EditTransaction {
    pub deleted_text: String,
    pub first_deleted_word: usize,
    pub last_deleted_word: usize,
    pub confidence: f32,
    pub accepted: bool,
    pub stripped_left_comma: bool,
    pub capitalized_right_word: bool,
}

#[derive(Debug)]
pub struct CleanResult {
    pub text: String,
    pub transactions: Vec<EditTransaction>,
    pub windows: usize,
    pub elapsed: Duration,
}

struct FdtRuntime {
    tokenizer: Tokenizer,
    session: Session,
}

/// A resident, serialized FDT runtime. It is deliberately independent from
/// Handy's speech-engine lifecycle, so an ASR idle unload does not evict it.
pub struct DisfluencyCleaner {
    runtime: Mutex<FdtRuntime>,
    threshold: f32,
    model_dir: PathBuf,
}

impl DisfluencyCleaner {
    /// Load and verify the pinned classifier artifacts.
    pub fn load(model_dir: impl AsRef<Path>) -> Result<Self> {
        let model_dir = model_dir.as_ref().to_path_buf();
        verify_artifact(
            &model_dir.join("model_quantized.onnx"),
            MODEL_SHA256,
            "model",
        )?;
        verify_artifact(
            &model_dir.join("tokenizer.json"),
            TOKENIZER_SHA256,
            "tokenizer",
        )?;
        verify_artifact(&model_dir.join("config.json"), CONFIG_SHA256, "config")?;
        validate_label_contract(&model_dir.join("config.json"))?;

        let mut tokenizer = Tokenizer::from_file(model_dir.join("tokenizer.json"))
            .map_err(|error| anyhow::anyhow!("failed to load FDT tokenizer: {error}"))?;
        tokenizer
            .with_truncation(Some(TruncationParams {
                max_length: MAX_TOKENS,
                stride: OVERLAP_TOKENS,
                strategy: TruncationStrategy::OnlyFirst,
                direction: TruncationDirection::Right,
            }))
            .map_err(|error| anyhow::anyhow!("failed to configure FDT tokenizer: {error}"))?;

        let session = Session::builder()
            .context("failed to create FDT ONNX session builder")?
            .with_optimization_level(GraphOptimizationLevel::All)
            .map_err(|error| anyhow::anyhow!("failed to optimize FDT ONNX session: {error}"))?
            .with_intra_threads(4)
            .map_err(|error| anyhow::anyhow!("failed to configure FDT ONNX threads: {error}"))?
            .with_parallel_execution(false)
            .map_err(|error| anyhow::anyhow!("failed to configure FDT ONNX execution: {error}"))?
            .commit_from_file(model_dir.join("model_quantized.onnx"))
            .context("failed to load FDT ONNX model")?;

        let threshold = configured_threshold();
        info!(
            "Loaded FDT cleanup model from '{}' (span threshold {:.2}, {}-token windows, {}-token overlap)",
            model_dir.display(),
            threshold,
            MAX_TOKENS,
            OVERLAP_TOKENS
        );

        Ok(Self {
            runtime: Mutex::new(FdtRuntime { tokenizer, session }),
            threshold,
            model_dir,
        })
    }

    pub fn model_dir(&self) -> &Path {
        &self.model_dir
    }

    /// Classify and decode a transcript. Panics inside the optional runtime are
    /// converted to errors so callers can execute the legacy fail-open path.
    pub fn clean(&self, text: &str) -> Result<CleanResult> {
        let mut runtime = self
            .runtime
            .lock()
            .map_err(|_| anyhow::anyhow!("FDT runtime mutex was poisoned"))?;
        let result = catch_unwind(AssertUnwindSafe(|| runtime.classify(text, self.threshold)))
            .map_err(|payload| {
            let message = payload
                .downcast_ref::<&str>()
                .copied()
                .or_else(|| payload.downcast_ref::<String>().map(String::as_str))
                .unwrap_or("unknown panic");
            anyhow::anyhow!("FDT runtime panicked: {message}")
        })??;

        let accepted = result
            .transactions
            .iter()
            .filter(|transaction| transaction.accepted)
            .count();
        if result.transactions.is_empty() {
            debug!(
                "FDT cleanup found no edit transactions in {:.2}ms across {} window(s)",
                result.elapsed.as_secs_f64() * 1_000.0,
                result.windows
            );
        } else {
            let decisions = result
                .transactions
                .iter()
                .map(|transaction| {
                    format!(
                        "{}:'{}'@{:.3}",
                        if transaction.accepted {
                            "accepted"
                        } else {
                            "rejected"
                        },
                        transaction.deleted_text,
                        transaction.confidence
                    )
                })
                .collect::<Vec<_>>()
                .join(", ");
            info!(
                "FDT cleanup accepted {}/{} transaction(s) in {:.2}ms across {} window(s): {}",
                accepted,
                result.transactions.len(),
                result.elapsed.as_secs_f64() * 1_000.0,
                result.windows,
                decisions
            );
        }

        Ok(result)
    }
}

impl FdtRuntime {
    fn classify(&mut self, text: &str, threshold: f32) -> Result<CleanResult> {
        let started = Instant::now();
        let words = text.split_whitespace().collect::<Vec<_>>();
        if words.is_empty() {
            return Ok(CleanResult {
                text: text.to_string(),
                transactions: Vec::new(),
                windows: 0,
                elapsed: started.elapsed(),
            });
        }

        let mut root = self
            .tokenizer
            .encode(words.clone(), true)
            .map_err(|error| anyhow::anyhow!("FDT tokenization failed: {error}"))?;
        let mut windows = Vec::new();
        collect_windows(&mut root, &mut windows);
        ensure!(!windows.is_empty(), "FDT tokenizer returned no windows");

        let mut best_predictions: Vec<Option<WordPrediction>> = vec![None; words.len()];
        for window in &windows {
            self.infer_window(window, &mut best_predictions)?;
        }

        let predictions = best_predictions
            .into_iter()
            .enumerate()
            .map(|(word_index, prediction)| {
                prediction.ok_or_else(|| {
                    anyhow::anyhow!(
                        "FDT produced no first-WordPiece prediction for word {word_index} ('{}')",
                        words[word_index]
                    )
                })
            })
            .collect::<Result<Vec<_>>>()?;
        let (cleaned, transactions) = decode_transactions(text, &words, &predictions, threshold);

        Ok(CleanResult {
            text: cleaned,
            transactions,
            windows: windows.len(),
            elapsed: started.elapsed(),
        })
    }

    fn infer_window(
        &mut self,
        encoding: &Encoding,
        best_predictions: &mut [Option<WordPrediction>],
    ) -> Result<()> {
        let sequence_length = encoding.len();
        ensure!(
            sequence_length <= MAX_TOKENS,
            "FDT window exceeded token limit"
        );

        let input_ids = encoding
            .get_ids()
            .iter()
            .map(|&value| i64::from(value))
            .collect::<Vec<_>>();
        let attention_mask = encoding
            .get_attention_mask()
            .iter()
            .map(|&value| i64::from(value))
            .collect::<Vec<_>>();
        let token_type_ids = encoding
            .get_type_ids()
            .iter()
            .map(|&value| i64::from(value))
            .collect::<Vec<_>>();

        let outputs = self.session.run(ort::inputs! {
            "input_ids" => Tensor::from_array(([1usize, sequence_length], input_ids))?,
            "attention_mask" => Tensor::from_array(([1usize, sequence_length], attention_mask))?,
            "token_type_ids" => Tensor::from_array(([1usize, sequence_length], token_type_ids))?,
        })?;
        let logits_value = outputs
            .get("logits")
            .ok_or_else(|| anyhow::anyhow!("FDT model did not return 'logits'"))?;
        let (shape, logits) = logits_value
            .try_extract_tensor::<f32>()
            .context("FDT logits were not a float tensor")?;
        ensure!(
            shape.len() == 3
                && shape[0] == 1
                && shape[1] == sequence_length as i64
                && shape[2] == LABEL_COUNT as i64,
            "unexpected FDT logits shape {shape}"
        );

        let word_ids = encoding.get_word_ids();
        let first_content = word_ids
            .iter()
            .position(Option::is_some)
            .ok_or_else(|| anyhow::anyhow!("FDT window contained no word tokens"))?;
        let last_content = word_ids
            .iter()
            .rposition(Option::is_some)
            .ok_or_else(|| anyhow::anyhow!("FDT window contained no word tokens"))?;

        for (position, maybe_word_id) in word_ids.iter().enumerate() {
            let Some(word_id) = maybe_word_id.map(|id| id as usize) else {
                continue;
            };
            ensure!(
                word_id < best_predictions.len(),
                "FDT tokenizer returned out-of-range word id {word_id}"
            );

            // The contract uses only the first WordPiece. An overflow window can
            // begin in the middle of a split word, so also reject explicit BERT
            // continuation pieces at the left edge.
            if (position > 0 && word_ids[position - 1] == *maybe_word_id)
                || encoding.get_tokens()[position].starts_with("##")
            {
                continue;
            }

            let context = (position - first_content).min(last_content - position);
            if best_predictions[word_id].is_some_and(|existing| existing.context >= context) {
                continue;
            }

            let offset = position * LABEL_COUNT;
            let probabilities = softmax4(
                logits[offset..offset + LABEL_COUNT]
                    .try_into()
                    .expect("four-label slice"),
            );
            let (label_index, confidence) = probabilities
                .iter()
                .copied()
                .enumerate()
                .max_by(|left, right| left.1.total_cmp(&right.1))
                .expect("four labels");
            best_predictions[word_id] = Some(WordPrediction {
                label: Label::from_index(label_index)?,
                confidence,
                context,
            });
        }

        Ok(())
    }
}

fn collect_windows(encoding: &mut Encoding, windows: &mut Vec<Encoding>) {
    let overflow = encoding.take_overflowing();
    windows.push(encoding.clone());
    for mut child in overflow {
        collect_windows(&mut child, windows);
    }
}

fn softmax4(logits: &[f32; LABEL_COUNT]) -> [f32; LABEL_COUNT] {
    let maximum = logits.iter().copied().fold(f32::NEG_INFINITY, f32::max);
    let mut probabilities = logits.map(|value| (value - maximum).exp());
    let total = probabilities.iter().sum::<f32>();
    for probability in &mut probabilities {
        *probability /= total;
    }
    probabilities
}

fn decode_transactions(
    original: &str,
    words: &[&str],
    predictions: &[WordPrediction],
    threshold: f32,
) -> (String, Vec<EditTransaction>) {
    debug_assert_eq!(words.len(), predictions.len());
    let mut applied = vec![Label::Keep; words.len()];
    let mut transactions = Vec::new();
    let mut index = 0;

    while index < predictions.len() {
        if predictions[index].label != Label::Delete {
            index += 1;
            continue;
        }

        let first_deleted = index;
        while index + 1 < predictions.len() && predictions[index + 1].label == Label::Delete {
            index += 1;
        }
        let last_deleted = index;
        let strip_left =
            first_deleted > 0 && predictions[first_deleted - 1].label == Label::StripComma;
        let capitalize_right = last_deleted + 1 < predictions.len()
            && predictions[last_deleted + 1].label == Label::Capitalize;

        let mut action_confidences = predictions[first_deleted..=last_deleted]
            .iter()
            .map(|prediction| prediction.confidence)
            .collect::<Vec<_>>();
        if strip_left {
            action_confidences.push(predictions[first_deleted - 1].confidence);
        }
        if capitalize_right {
            action_confidences.push(predictions[last_deleted + 1].confidence);
        }
        let confidence = geometric_mean(&action_confidences);
        let accepted = confidence >= threshold;

        if accepted {
            for edit in &mut applied[first_deleted..=last_deleted] {
                *edit = Label::Delete;
            }
            if strip_left {
                applied[first_deleted - 1] = Label::StripComma;
            }
            if capitalize_right {
                applied[last_deleted + 1] = Label::Capitalize;
            }
        }

        transactions.push(EditTransaction {
            deleted_text: words[first_deleted..=last_deleted].join(" "),
            first_deleted_word: first_deleted,
            last_deleted_word: last_deleted,
            confidence,
            accepted,
            stripped_left_comma: strip_left,
            capitalized_right_word: capitalize_right,
        });
        index += 1;
    }

    if !transactions.iter().any(|transaction| transaction.accepted) {
        return (original.to_string(), transactions);
    }

    let cleaned = words
        .iter()
        .zip(applied)
        .filter_map(|(word, action)| match action {
            Label::Keep => Some((*word).to_string()),
            Label::Delete => None,
            Label::StripComma => Some(strip_one_trailing_comma(word)),
            Label::Capitalize => Some(capitalize_first_char(word)),
        })
        .collect::<Vec<_>>()
        .join(" ");
    (cleaned, transactions)
}

fn geometric_mean(values: &[f32]) -> f32 {
    debug_assert!(!values.is_empty());
    (values
        .iter()
        .map(|value| value.max(f32::MIN_POSITIVE).ln())
        .sum::<f32>()
        / values.len() as f32)
        .exp()
}

fn strip_one_trailing_comma(word: &str) -> String {
    word.strip_suffix(',').unwrap_or(word).to_string()
}

fn capitalize_first_char(word: &str) -> String {
    let mut chars = word.chars();
    let Some(first) = chars.next() else {
        return String::new();
    };
    first.to_uppercase().chain(chars).collect()
}

fn configured_threshold() -> f32 {
    match std::env::var("HANDY_FDT_MIN_SPAN_CONFIDENCE") {
        Ok(raw) => match raw.parse::<f32>() {
            Ok(value) if (0.0..=1.0).contains(&value) => value,
            _ => {
                log::warn!(
                    "Ignoring invalid HANDY_FDT_MIN_SPAN_CONFIDENCE='{}'; using {:.2}",
                    raw,
                    DEFAULT_SPAN_CONFIDENCE
                );
                DEFAULT_SPAN_CONFIDENCE
            }
        },
        Err(_) => DEFAULT_SPAN_CONFIDENCE,
    }
}

fn verify_artifact(path: &Path, expected: &str, name: &str) -> Result<()> {
    let bytes = fs::read(path)
        .with_context(|| format!("missing pinned FDT {name} artifact '{}'", path.display()))?;
    let actual = Sha256::digest(&bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    ensure!(
        actual == expected,
        "FDT {name} checksum mismatch for '{}': expected {expected}, got {actual}",
        path.display()
    );
    Ok(())
}

fn validate_label_contract(config_path: &Path) -> Result<()> {
    let config: serde_json::Value = serde_json::from_slice(
        &fs::read(config_path)
            .with_context(|| format!("failed to read '{}'", config_path.display()))?,
    )
    .context("failed to parse FDT config")?;
    let expected = ["KEEP", "DELETE", "KEEP_STRIP_COMMA", "KEEP_CAPITALIZE"];
    for (index, label) in expected.iter().enumerate() {
        ensure!(
            config["id2label"][index.to_string()] == *label,
            "unexpected FDT label contract at index {index}: expected {label}"
        );
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn prediction(label: Label, confidence: f32) -> WordPrediction {
        WordPrediction {
            label,
            confidence,
            context: 0,
        }
    }

    #[test]
    fn accepts_complete_delete_and_repair_transaction() {
        let text = "This is, I mean, actually useful.";
        let words = text.split_whitespace().collect::<Vec<_>>();
        let predictions = vec![
            prediction(Label::Keep, 0.99),
            prediction(Label::StripComma, 0.92),
            prediction(Label::Delete, 0.91),
            prediction(Label::Delete, 0.89),
            prediction(Label::Capitalize, 0.94),
            prediction(Label::Keep, 0.99),
        ];

        let (cleaned, transactions) = decode_transactions(text, &words, &predictions, 0.70);

        assert_eq!(cleaned, "This is Actually useful.");
        assert_eq!(transactions.len(), 1);
        assert!(transactions[0].accepted);
        assert!(transactions[0].stripped_left_comma);
        assert!(transactions[0].capitalized_right_word);
    }

    #[test]
    fn rejects_all_parts_of_low_confidence_transaction() {
        let text = "This is, I mean, actually useful.";
        let words = text.split_whitespace().collect::<Vec<_>>();
        let predictions = vec![
            prediction(Label::Keep, 0.99),
            prediction(Label::StripComma, 0.92),
            prediction(Label::Delete, 0.91),
            prediction(Label::Delete, 0.10),
            prediction(Label::Capitalize, 0.94),
            prediction(Label::Keep, 0.99),
        ];

        let (cleaned, transactions) = decode_transactions(text, &words, &predictions, 0.70);

        assert_eq!(cleaned, text);
        assert!(!transactions[0].accepted);
    }

    #[test]
    fn ignores_orphan_repairs() {
        let text = "Keep, this sentence.";
        let words = text.split_whitespace().collect::<Vec<_>>();
        let predictions = vec![
            prediction(Label::StripComma, 0.99),
            prediction(Label::Capitalize, 0.99),
            prediction(Label::Keep, 0.99),
        ];

        let (cleaned, transactions) = decode_transactions(text, &words, &predictions, 0.70);

        assert_eq!(cleaned, text);
        assert!(transactions.is_empty());
    }

    #[test]
    fn strips_only_one_final_comma() {
        assert_eq!(strip_one_trailing_comma("1,000,"), "1,000");
        assert_eq!(strip_one_trailing_comma("1,000"), "1,000");
        assert_eq!(strip_one_trailing_comma("word,,"), "word,");
    }

    #[test]
    fn capitalization_handles_unicode_without_touching_the_tail() {
        assert_eq!(capitalize_first_char("élan"), "Élan");
        assert_eq!(capitalize_first_char("ßeta"), "SSeta");
        assert_eq!(capitalize_first_char(""), "");
    }

    #[test]
    fn geometric_mean_does_not_penalize_long_spans_by_multiplication() {
        let short = geometric_mean(&[0.8, 0.8]);
        let long = geometric_mean(&[0.8; 20]);
        assert!((short - long).abs() < 1e-6);
    }

    #[test]
    fn softmax_is_stable_for_large_logits() {
        let probabilities = softmax4(&[10_000.0, 9_999.0, 0.0, -10_000.0]);
        assert!((probabilities.iter().sum::<f32>() - 1.0).abs() < 1e-6);
        assert!(probabilities[0] > probabilities[1]);
    }

    fn pinned_model_dir() -> PathBuf {
        std::env::var_os("HANDY_FDT_MODEL_DIR")
            .map(PathBuf::from)
            .or_else(|| {
                std::env::var_os("HOME").map(|home| {
                    PathBuf::from(home)
                        .join(".local/share/com.pais.handy")
                        .join(MODEL_SUBDIR)
                })
            })
            .expect("HANDY_FDT_MODEL_DIR or HOME must be set")
    }

    #[test]
    #[ignore = "requires the pinned local model"]
    fn pinned_model_cleans_natural_disfluency() {
        let cleaner = DisfluencyCleaner::load(pinned_model_dir()).expect("load pinned model");
        let result = cleaner
            .clean("I I think this is, you know, genuinely useful.")
            .expect("clean transcript");

        assert_eq!(result.text, "I think this is genuinely useful.");
        assert!(result.windows >= 1);
    }

    #[test]
    #[ignore = "requires the pinned local model"]
    fn pinned_model_covers_the_tail_of_long_transcripts() {
        let cleaner = DisfluencyCleaner::load(pinned_model_dir()).expect("load pinned model");
        let prefix = std::iter::repeat_n("This is an ordinary sentence.", 80)
            .collect::<Vec<_>>()
            .join(" ");
        let transcript = format!("{prefix} I I think the tail remains covered.");
        let result = cleaner.clean(&transcript).expect("clean long transcript");

        assert!(result.windows > 1);
        assert!(result.text.ends_with("I think the tail remains covered."));
    }
}
