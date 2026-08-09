use anyhow::Result;
use hound::{WavReader, WavSpec, WavWriter};
use log::debug;
use std::io::{Seek, Write};
use std::path::Path;

/// Read a WAV file and return normalised f32 samples.
pub fn read_wav_samples<P: AsRef<Path>>(file_path: P) -> Result<Vec<f32>> {
    let reader = WavReader::open(file_path.as_ref())?;
    let samples = reader
        .into_samples::<i16>()
        .map(|s| s.map(|v| v as f32 / i16::MAX as f32))
        .collect::<Result<Vec<f32>, _>>()?;
    Ok(samples)
}

/// Verify a WAV file by reading it back and checking the sample count.
pub fn verify_wav_file<P: AsRef<Path>>(file_path: P, expected_samples: usize) -> Result<()> {
    let reader = WavReader::open(file_path.as_ref())?;
    let actual_samples = reader.len() as usize;
    if actual_samples != expected_samples {
        anyhow::bail!(
            "WAV sample count mismatch: expected {}, got {}",
            expected_samples,
            actual_samples
        );
    }
    Ok(())
}

/// Write audio samples through a destination the caller already owns.
///
/// This seam does not resolve or reopen a path, so an exclusively reserved
/// recording cannot accidentally truncate a different file.
pub fn write_wav_file<W: Write + Seek>(writer: W, samples: &[f32]) -> Result<()> {
    let spec = WavSpec {
        channels: 1,
        sample_rate: 16000,
        bits_per_sample: 16,
        sample_format: hound::SampleFormat::Int,
    };

    let mut writer = WavWriter::new(writer, spec)?;

    // Convert f32 samples to i16 for WAV
    for sample in samples {
        let sample_i16 = (sample * i16::MAX as f32) as i16;
        writer.write_sample(sample_i16)?;
    }

    writer.finalize()?;
    Ok(())
}

/// Save audio samples to a path the caller legitimately owns.
pub fn save_wav_file<P: AsRef<Path>>(file_path: P, samples: &[f32]) -> Result<()> {
    let file = std::fs::File::create(file_path.as_ref())?;
    write_wav_file(file, samples)?;
    debug!("Saved WAV file: {:?}", file_path.as_ref());
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs::{self, OpenOptions};
    use tempfile::TempDir;

    #[test]
    fn wav_writer_uses_supplied_exclusive_file_without_reopening_an_owned_path() {
        let temp_dir = TempDir::new().expect("create temporary directory");
        let owned_path = temp_dir.path().join("history-owned.wav");
        let reserved_path = temp_dir.path().join("reserved.wav");
        fs::write(&owned_path, b"history-owned bytes").expect("write owned fixture");
        let reserved = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&reserved_path)
            .expect("exclusively create reserved destination");

        write_wav_file(reserved, &[0.0, 0.25, -0.25]).expect("write reserved WAV handle");

        assert_eq!(fs::read(&owned_path).unwrap(), b"history-owned bytes");
        assert_eq!(read_wav_samples(&reserved_path).unwrap().len(), 3);
    }
}
