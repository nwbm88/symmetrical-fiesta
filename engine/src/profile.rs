//! Reference profiles: fingerprint the tonal balance of clean studio tracks,
//! then compute a matching EQ that morphs a rough recording toward that
//! sound. Build a profile once from a few songs by the same artist (or any
//! well-mastered material you like), and every remaster can use it.

use anyhow::{anyhow, Result};
use serde::{Deserialize, Serialize};

use crate::dsp::{match_centers, Biquad, BiquadCoeffs, MATCH_BANDS};

/// Analyze at most this much audio per track — plenty for a stable average.
const MAX_MEASURE_SECS: f32 = 240.0;

/// Normalization reference range: the level of these mid bands defines 0 dB,
/// so profiles compare tonal *shape* independent of how loud tracks are.
const NORM_LO_HZ: f32 = 400.0;
const NORM_HI_HZ: f32 = 4000.0;

/// Per-band gain limits for the matching EQ. Restoration, not surgery.
const MAX_MATCH_GAIN_DB: f32 = 9.0;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct Profile {
    pub version: u32,
    pub name: String,
    pub num_tracks: u32,
    /// Normalized band spectrum (dB relative to the mid-band average),
    /// one value per `match_centers()` band.
    pub band_db: Vec<f32>,
}

impl Default for Profile {
    fn default() -> Self {
        Self {
            version: 1,
            name: String::new(),
            num_tracks: 0,
            band_db: Vec::new(),
        }
    }
}

/// RMS level in dB for each of the given band centers (constant-Q bandpass
/// bank, single pass over the mono mix of interleaved stereo samples).
pub fn measure_bands(sample_rate: u32, interleaved: &[f32], centers: &[f32]) -> Vec<f32> {
    let fs = sample_rate as f32;
    let max_samples = ((MAX_MEASURE_SECS * fs) as usize * 2).min(interleaved.len());
    let data = &interleaved[..max_samples];

    let mut filters: Vec<Biquad> = centers
        .iter()
        .map(|&f| Biquad::with_coeffs(BiquadCoeffs::bandpass(fs, f, 2.0)))
        .collect();
    let mut acc = vec![0f64; centers.len()];
    let frames = (data.len() / 2).max(1);
    for f in data.chunks_exact(2) {
        let m = (f[0] + f[1]) * 0.5;
        for (k, filt) in filters.iter_mut().enumerate() {
            let y = filt.process(m);
            acc[k] += (y * y) as f64;
        }
    }
    acc.iter()
        .map(|a| 20.0 * (((a / frames as f64).sqrt()) as f32).max(1e-9).log10())
        .collect()
}

/// Shift a band curve so the average of the mid bands sits at 0 dB.
pub fn normalize_bands(centers: &[f32], bands: &[f32]) -> Vec<f32> {
    let mids: Vec<f32> = centers
        .iter()
        .zip(bands)
        .filter(|(c, _)| **c >= NORM_LO_HZ && **c <= NORM_HI_HZ)
        .map(|(_, b)| *b)
        .collect();
    let ref_level = if mids.is_empty() {
        bands.iter().sum::<f32>() / bands.len().max(1) as f32
    } else {
        mids.iter().sum::<f32>() / mids.len() as f32
    };
    bands.iter().map(|b| b - ref_level).collect()
}

/// Average several tracks' measurements into one profile.
pub fn profile_from_tracks(name: &str, per_track_bands: &[Vec<f32>]) -> Result<Profile> {
    if per_track_bands.is_empty() {
        return Err(anyhow!("no tracks to build a profile from"));
    }
    let centers = match_centers();
    let mut avg = vec![0f32; MATCH_BANDS];
    for bands in per_track_bands {
        if bands.len() != MATCH_BANDS {
            return Err(anyhow!("band count mismatch"));
        }
        for (a, b) in avg.iter_mut().zip(normalize_bands(&centers, bands)) {
            *a += b;
        }
    }
    for a in &mut avg {
        *a /= per_track_bands.len() as f32;
    }
    Ok(Profile {
        version: 1,
        name: name.to_string(),
        num_tracks: per_track_bands.len() as u32,
        band_db: avg,
    })
}

/// Per-band matching gains that move `track_bands` (raw measurement of the
/// track being remastered) toward the profile. Smoothed across neighbors and
/// clamped so it stays restoration rather than butchery.
pub fn match_gains(profile: &Profile, track_bands: &[f32], strength: f32) -> Result<Vec<f32>> {
    if profile.band_db.len() != MATCH_BANDS || track_bands.len() != MATCH_BANDS {
        return Err(anyhow!("profile/track band count mismatch"));
    }
    let centers = match_centers();
    let track_norm = normalize_bands(&centers, track_bands);
    let strength = strength.clamp(0.0, 1.5);

    let raw: Vec<f32> = profile
        .band_db
        .iter()
        .zip(&track_norm)
        .map(|(p, t)| ((p - t) * strength).clamp(-MAX_MATCH_GAIN_DB, MAX_MATCH_GAIN_DB))
        .collect();

    // 3-point smoothing avoids comb-like adjacent boost/cut patterns.
    let n = raw.len();
    let mut out = vec![0f32; n];
    for i in 0..n {
        let prev = raw[i.saturating_sub(1)];
        let next = raw[(i + 1).min(n - 1)];
        out[i] = 0.25 * prev + 0.5 * raw[i] + 0.25 * next;
    }
    Ok(out)
}
