//! Offline rendering: applies the same chain as live playback, but with an
//! exact two-pass loudness normalization, then writes the result to disk.

use std::path::Path;
use std::process::Command;

use anyhow::{anyhow, Context, Result};

use crate::dsp::{mix_denoise, DspChain};
use crate::loudness;
use crate::preset::Preset;
use crate::AudioData;

const BLOCK: usize = 4096;

/// Render dry (+ optional AI-denoised) audio through the full chain.
///
/// Two-pass loudness: the chain runs with no normalization gain first, the
/// result is measured, then the exact gain to hit `target_lufs` is applied
/// and the limiter is run as the final stage. This is what makes every track
/// of an album land at the same perceived loudness.
pub fn render(dry: &AudioData, wet: Option<&AudioData>, preset: &Preset) -> Result<AudioData> {
    let n = dry.samples.len();
    let mut buf = vec![0f32; n];
    match wet {
        Some(w) if preset.denoise_amount > 0.0 => {
            mix_denoise(&dry.samples, &w.samples, preset.denoise_amount, &mut buf)
        }
        _ => buf.copy_from_slice(&dry.samples),
    }

    // Pass 1: HPF -> EQ -> compressor -> trim (no loudness gain, no limiter).
    let mut pass1 = preset.clone();
    pass1.loudness_enabled = false;
    pass1.limiter_ceiling_db = 40.0; // effectively bypassed
    let mut chain = DspChain::new(dry.sample_rate);
    for block in buf.chunks_mut(BLOCK * 2) {
        chain.process_block(block, &pass1, 0.0);
    }

    // Pass 2: measure, apply exact normalization gain, then the real limiter.
    let mut gain_db = 0.0f64;
    if preset.loudness_enabled {
        let measured = loudness::integrated_lufs(&AudioData {
            sample_rate: dry.sample_rate,
            samples: buf.clone(),
        })?;
        gain_db = loudness::normalization_gain_db(measured, preset.target_lufs as f64);
    }
    let gain = 10f64.powf(gain_db / 20.0) as f32;

    let mut limit_only = Preset {
        hpf_enabled: false,
        eq_enabled: false,
        comp_enabled: false,
        loudness_enabled: false,
        output_gain_db: 0.0,
        // Already applied in pass 1 — must not run twice.
        match_enabled: false,
        match_gains: Vec::new(),
        ..preset.clone()
    };
    limit_only.limiter_ceiling_db = preset.limiter_ceiling_db;
    let mut limiter_chain = DspChain::new(dry.sample_rate);
    for s in &mut buf {
        *s *= gain;
    }
    for block in buf.chunks_mut(BLOCK * 2) {
        limiter_chain.process_block(block, &limit_only, 0.0);
    }

    Ok(AudioData {
        sample_rate: dry.sample_rate,
        samples: buf,
    })
}

/// Write audio as 24-bit WAV.
pub fn write_wav24(audio: &AudioData, path: &Path) -> Result<()> {
    let spec = hound::WavSpec {
        channels: 2,
        sample_rate: audio.sample_rate,
        bits_per_sample: 24,
        sample_format: hound::SampleFormat::Int,
    };
    let mut writer =
        hound::WavWriter::create(path, spec).with_context(|| format!("creating {}", path.display()))?;
    const MAX: f32 = 8_388_607.0; // 2^23 - 1
    for &s in &audio.samples {
        writer.write_sample((s.clamp(-1.0, 1.0) * MAX) as i32)?;
    }
    writer.finalize()?;
    Ok(())
}

/// True if a command is runnable (used to detect optional external tools:
/// ffmpeg for mp3/flac export, deep-filter / demucs for heavy AI cleanup).
pub fn tool_available(cmd: &str, probe_arg: &str) -> bool {
    Command::new(cmd)
        .arg(probe_arg)
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

/// Export to the requested format. WAV is built in; mp3/flac/m4a are encoded
/// with ffmpeg from a temporary WAV when ffmpeg is present on the system.
pub fn export(audio: &AudioData, path: &Path, format: &str) -> Result<()> {
    match format {
        "wav" => write_wav24(audio, path),
        "mp3" | "flac" | "m4a" => {
            if !tool_available("ffmpeg", "-version") {
                return Err(anyhow!(
                    "ffmpeg not found on this system — export as WAV, or install ffmpeg to enable {} export",
                    format
                ));
            }
            let tmp = path.with_extension("clearwave-tmp.wav");
            write_wav24(audio, &tmp)?;
            let mut cmd = Command::new("ffmpeg");
            cmd.arg("-y").arg("-i").arg(&tmp);
            match format {
                "mp3" => {
                    cmd.args(["-codec:a", "libmp3lame", "-q:a", "0"]);
                }
                "flac" => {
                    cmd.args(["-codec:a", "flac"]);
                }
                "m4a" => {
                    cmd.args(["-codec:a", "aac", "-b:a", "256k"]);
                }
                _ => unreachable!(),
            }
            cmd.arg(path);
            let status = cmd
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null())
                .status()
                .context("running ffmpeg")?;
            let _ = std::fs::remove_file(&tmp);
            if !status.success() {
                return Err(anyhow!("ffmpeg encoding failed"));
            }
            Ok(())
        }
        other => Err(anyhow!("unsupported export format: {other}")),
    }
}

/// Run an external AI pre-processing command on a track (e.g. DeepFilterNet's
/// `deep-filter`, or any custom tool). The command template supports three
/// placeholders:
///   {in}     input WAV path
///   {out}    output WAV path the tool should write
///   {outdir} output directory, for tools (like deep-filter) that keep the
///            input's file name and only take a destination folder
/// `pick` selects among multiple outputs (case-insensitive substring of the
/// file name) — needed for stem separators that write e.g. "(No Crowd)" and
/// "(Crowd)" files into {outdir}.
/// Returns the processed audio, resampled back to the input rate.
pub fn external_preprocess(
    audio: &AudioData,
    command_template: &str,
    work_dir: &Path,
    pick: Option<&str>,
) -> Result<AudioData> {
    if !command_template.contains("{in}")
        || !(command_template.contains("{out}") || command_template.contains("{outdir}"))
    {
        return Err(anyhow!(
            "command template must contain {{in}} and either {{out}} or {{outdir}}"
        ));
    }
    let out_dir = work_dir.join("ai_out");
    let _ = std::fs::remove_dir_all(&out_dir);
    std::fs::create_dir_all(&out_dir)?;
    let in_path = work_dir.join("clearwave_track.wav");
    let out_path = out_dir.join("clearwave_track_out.wav");
    write_wav24(audio, &in_path)?;

    let cmdline = command_template
        .replace("{in}", &in_path.to_string_lossy())
        .replace("{out}", &out_path.to_string_lossy())
        .replace("{outdir}", &out_dir.to_string_lossy());
    let parts = shell_words(&cmdline);
    if parts.is_empty() {
        return Err(anyhow!("empty external command"));
    }
    let status = Command::new(&parts[0])
        .args(&parts[1..])
        .status()
        .with_context(|| format!("running external tool `{}`", parts[0]))?;
    if !status.success() {
        return Err(anyhow!("external tool exited with an error"));
    }

    // Locate the tool's output. Priority: a file matching `pick`, then the
    // exact {out} path, then <outdir>/<input name>, then a single leftover
    // audio file. Stem separators often nest outputs, so search recursively.
    let candidates = collect_audio_files(&out_dir, 3);
    let produced = if let Some(needle) = pick.filter(|p| !p.trim().is_empty()) {
        let needle = needle.to_lowercase();
        candidates
            .iter()
            .find(|p| {
                p.file_name()
                    .map(|n| n.to_string_lossy().to_lowercase().contains(&needle))
                    .unwrap_or(false)
            })
            .cloned()
            .ok_or_else(|| {
                anyhow!(
                    "no output file matching \"{}\" — tool produced: {}",
                    needle,
                    candidates
                        .iter()
                        .filter_map(|p| p.file_name().map(|n| n.to_string_lossy().into_owned()))
                        .collect::<Vec<_>>()
                        .join(", ")
                )
            })?
    } else if out_path.exists() {
        out_path.clone()
    } else {
        let same_name = out_dir.join("clearwave_track.wav");
        if same_name.exists() {
            same_name
        } else {
            candidates
                .into_iter()
                .next()
                .ok_or_else(|| anyhow!("external tool produced no output file"))?
        }
    };

    let processed = crate::decode::decode_file(&produced)?;
    let mut resampled = crate::resample::resample(&processed, audio.sample_rate)?;
    resampled.samples.resize(audio.samples.len(), 0.0);
    let _ = std::fs::remove_file(&in_path);
    let _ = std::fs::remove_dir_all(&out_dir);
    Ok(resampled)
}

fn collect_audio_files(dir: &Path, depth: usize) -> Vec<std::path::PathBuf> {
    let mut out = Vec::new();
    if depth == 0 {
        return out;
    }
    if let Ok(entries) = std::fs::read_dir(dir) {
        for entry in entries.flatten() {
            let p = entry.path();
            if p.is_dir() {
                out.extend(collect_audio_files(&p, depth - 1));
            } else if p
                .extension()
                .and_then(|e| e.to_str())
                .map(|e| matches!(e.to_lowercase().as_str(), "wav" | "flac" | "mp3" | "m4a" | "ogg"))
                .unwrap_or(false)
            {
                out.push(p);
            }
        }
    }
    out.sort();
    out
}

/// Minimal shell-style splitter supporting double quotes (enough for
/// command templates with paths that contain spaces).
fn shell_words(s: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut cur = String::new();
    let mut in_quotes = false;
    for c in s.chars() {
        match c {
            '"' => in_quotes = !in_quotes,
            c if c.is_whitespace() && !in_quotes => {
                if !cur.is_empty() {
                    out.push(std::mem::take(&mut cur));
                }
            }
            c => cur.push(c),
        }
    }
    if !cur.is_empty() {
        out.push(cur);
    }
    out
}
