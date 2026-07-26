//! Offline processing pipeline shared by single-track export and batch:
//! decode -> 48 kHz -> optional external AI tool -> RNNoise denoise twin
//! -> DSP chain with exact loudness normalization -> encode.

use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use clearwave_engine::preset::Preset;
use clearwave_engine::{decode, denoise, render, resample};

const RENDER_RATE: u32 = 48_000;

pub fn process_and_export(
    input: &Path,
    output: &Path,
    format: &str,
    preset: &Preset,
    external_cmd: Option<&str>,
) -> Result<()> {
    let native = decode::decode_file(input).with_context(|| format!("decoding {}", input.display()))?;
    let mut dry = resample::resample(&native, RENDER_RATE)?;
    drop(native);

    if let Some(cmd) = external_cmd.filter(|c| !c.trim().is_empty()) {
        let work = std::env::temp_dir().join("clearwave-ai");
        dry = render::external_preprocess(&dry, cmd, &work)
            .context("external AI pre-processing failed")?;
    }

    let wet = if preset.denoise_amount > 0.0 {
        Some(denoise::denoise(&dry)?)
    } else {
        None
    };

    let out = render::render(&dry, wet.as_ref(), preset)?;
    if let Some(parent) = output.parent() {
        std::fs::create_dir_all(parent)?;
    }
    render::export(&out, output, format).with_context(|| format!("writing {}", output.display()))
}

pub fn batch_output_path(input: &Path, output_dir: &Path, format: &str) -> PathBuf {
    let stem = input
        .file_stem()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_else(|| "track".into());
    output_dir.join(format!("{stem} [remastered].{format}"))
}
