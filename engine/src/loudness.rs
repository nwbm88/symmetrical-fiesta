use anyhow::{anyhow, Result};
use ebur128::{EbuR128, Mode};

use crate::AudioData;

/// Integrated loudness (EBU R128, LUFS) of interleaved stereo audio.
pub fn integrated_lufs(audio: &AudioData) -> Result<f64> {
    let mut meter = EbuR128::new(2, audio.sample_rate, Mode::I)?;
    meter.add_frames_f32(&audio.samples)?;
    match meter.loudness_global() {
        Ok(l) if l.is_finite() => Ok(l),
        Ok(_) => Err(anyhow!("track is silent; loudness undefined")),
        Err(e) => Err(e.into()),
    }
}

/// Gain in dB required to move `measured` LUFS to `target` LUFS.
pub fn normalization_gain_db(measured: f64, target: f64) -> f64 {
    (target - measured).clamp(-40.0, 40.0)
}
