//! Offline processing pipeline shared by single-track export and batch:
//! decode -> 48 kHz -> optional external AI tool -> RNNoise denoise twin
//! -> DSP chain with exact loudness normalization -> encode.

use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use clearwave_engine::preset::Preset;
use clearwave_engine::profile::{self, Profile};
use clearwave_engine::{decode, denoise, dsp, render, resample};
use serde::Deserialize;

const RENDER_RATE: u32 = 48_000;

/// External AI stage configuration sent by the UI.
#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct ExternalOptions {
    /// "none" | "single" (one in→out tool) | "stems" (stem rescue)
    pub mode: String,
    // single-tool mode
    pub cmd: String,
    pub pick: String,
    // stem-rescue mode
    pub separator_cmd: String,
    pub voice_cmd: String,
    pub vocal_gain_db: f32,
    pub instrumental_gain_db: f32,
}

pub fn process_and_export(
    input: &Path,
    output: &Path,
    format: &str,
    preset: &Preset,
    external: Option<&ExternalOptions>,
    reference: Option<&Profile>,
) -> Result<()> {
    let native = decode::decode_file(input).with_context(|| format!("decoding {}", input.display()))?;
    let mut dry = resample::resample(&native, RENDER_RATE)?;
    drop(native);

    let work = std::env::temp_dir().join("clearwave-ai");
    match external.map(|e| e.mode.as_str()).unwrap_or("none") {
        "single" => {
            let e = external.unwrap();
            if !e.cmd.trim().is_empty() {
                let pick = if e.pick.trim().is_empty() { None } else { Some(e.pick.as_str()) };
                dry = render::external_preprocess(&dry, &e.cmd, &work, pick)
                    .context("external AI pre-processing failed")?;
            }
        }
        "stems" => {
            let e = external.unwrap();
            let opts = render::StemRescue {
                separator_cmd: e.separator_cmd.clone(),
                voice_cmd: if e.voice_cmd.trim().is_empty() {
                    None
                } else {
                    Some(e.voice_cmd.clone())
                },
                vocal_gain_db: e.vocal_gain_db,
                instrumental_gain_db: e.instrumental_gain_db,
            };
            dry = render::stem_rescue(&dry, &opts, &work).context("stem rescue failed")?;
        }
        _ => {}
    }

    let wet = if preset.denoise_amount > 0.0 {
        Some(denoise::denoise(&dry)?)
    } else {
        None
    };

    // Reference matching is per-track: measure this track and recompute the
    // gains, so every album track is pulled toward the same target sound.
    let mut preset = preset.clone();
    if preset.match_enabled {
        if let Some(p) = reference {
            let bands = profile::measure_bands(RENDER_RATE, &dry.samples, &dsp::match_centers());
            preset.match_gains = profile::match_gains(p, &bands, preset.match_strength)?;
        }
        // Without a profile, keep whatever gains the UI computed for this track.
    }

    let out = render::render(&dry, wet.as_ref(), &preset)?;
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
