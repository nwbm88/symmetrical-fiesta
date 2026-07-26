//! Auto mode: measure a track and propose a full restoration preset.
//!
//! Three measurements drive the decisions:
//!  1. Noise — the floor is estimated from the quietest 200 ms windows and
//!     cross-checked against how much energy the RNNoise pass actually
//!     removed, so clean material is never over-denoised.
//!  2. Spectral balance — RMS in 9 constant-Q bands, compared against the
//!     mid bands (well-mastered music is roughly flat in constant-Q bands).
//!  3. Dynamics — the spread between loud and quiet windows decides whether
//!     compression is warranted and how much.

use serde::Serialize;

use crate::dsp::{Biquad, BiquadCoeffs};
use crate::preset::Preset;

#[derive(Clone, Debug, Serialize)]
pub struct AutoReport {
    pub preset: Preset,
    pub notes: Vec<String>,
    pub snr_db: f32,
    pub noise_floor_db: f32,
    pub dynamic_spread_db: f32,
    pub band_db: Vec<f32>,
}

pub const BAND_CENTERS: [f32; 9] = [
    50.0, 120.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 12000.0,
];

const WINDOW_SECS: f32 = 0.2;
/// Analyze at most this much audio — plenty for stable statistics.
const MAX_ANALYZE_SECS: f32 = 240.0;
const SILENCE_FLOOR_DB: f32 = -85.0;

/// Compute an automatic preset from interleaved stereo buffers.
/// `wet` is the RNNoise-denoised twin (same length), if available.
/// `base` carries the user's current loudness target and limiter ceiling.
pub fn auto_preset(sample_rate: u32, dry: &[f32], wet: Option<&[f32]>, base: &Preset) -> AutoReport {
    let fs = sample_rate as f32;
    let max_samples = ((MAX_ANALYZE_SECS * fs) as usize * 2).min(dry.len());
    let dry = &dry[..max_samples];
    let wet = wet.filter(|w| w.len() >= max_samples).map(|w| &w[..max_samples]);

    let mut notes = Vec::new();
    let mut p = Preset {
        target_lufs: base.target_lufs,
        limiter_ceiling_db: base.limiter_ceiling_db,
        output_gain_db: 0.0,
        loudness_enabled: true,
        ..Preset::default()
    };

    // ---------- window RMS statistics (mono mix) ----------
    let win = (WINDOW_SECS * fs) as usize;
    let mut window_db: Vec<f32> = Vec::new();
    let mut i = 0;
    while i + win * 2 <= dry.len() {
        let mut acc = 0f64;
        for f in dry[i..i + win * 2].chunks_exact(2) {
            let m = (f[0] + f[1]) * 0.5;
            acc += (m * m) as f64;
        }
        let rms = (acc / win as f64).sqrt() as f32;
        let db = 20.0 * rms.max(1e-9).log10();
        if db > SILENCE_FLOOR_DB {
            window_db.push(db);
        }
        i += win * 2;
    }
    let noise_floor_db = percentile(&window_db, 10.0).unwrap_or(-80.0);
    let signal_db = percentile(&window_db, 90.0).unwrap_or(-20.0);
    let snr_db = signal_db - noise_floor_db;

    // ---------- denoise amount ----------
    // Heuristic SNR mapping, capped by how much RNNoise actually found.
    // A floor below -65 dB is inaudible — treat as clean regardless of SNR.
    let amount_from_snr = if noise_floor_db < -65.0 {
        0.0
    } else {
        ((45.0 - snr_db) / 30.0).clamp(0.0, 0.75)
    };
    let removal_cap = match wet {
        Some(w) => {
            let rms_dry = rms(dry);
            let rms_diff = rms_diff(dry, w);
            let frac = if rms_dry > 1e-9 { rms_diff / rms_dry } else { 0.0 };
            if frac < 0.05 {
                0.15 // the network barely touched it: it's already clean
            } else if frac < 0.15 {
                0.4
            } else {
                0.75
            }
        }
        None => 0.5,
    };
    p.denoise_amount = quantize(amount_from_snr.min(removal_cap), 0.05);
    if p.denoise_amount >= 0.05 {
        notes.push(format!(
            "Noise floor {:.0} dB (SNR {:.0} dB) → AI denoise {}%",
            noise_floor_db,
            snr_db,
            (p.denoise_amount * 100.0).round()
        ));
    } else {
        p.denoise_amount = 0.0;
        notes.push(format!(
            "Recording is clean (SNR {snr_db:.0} dB) → AI denoise off"
        ));
    }

    // ---------- band balance ----------
    let band_db = band_levels(fs, dry);
    // Mid reference: 250 Hz – 2 kHz average.
    let mids = (band_db[2] + band_db[3] + band_db[4] + band_db[5]) / 4.0;
    let sub = band_db[0];
    let bass = band_db[1];
    let presence = band_db[6];
    let air = (band_db[7] + band_db[8]) / 2.0;

    // Rumble: strong sub-band energy pushes the high-pass up.
    p.hpf_enabled = true;
    if sub > mids + 3.0 {
        p.hpf_freq = 60.0;
        notes.push("Strong low-frequency rumble → high-pass at 60 Hz".into());
    } else {
        p.hpf_freq = 30.0;
        notes.push("No significant rumble → gentle 30 Hz high-pass".into());
    }

    let mut eq_used = false;
    // Muffled: air far below the mids → high-shelf boost.
    if air < mids - 14.0 {
        p.high_shelf_freq = 8000.0;
        p.high_shelf_gain_db = quantize(((mids - 14.0 - air) * 0.4).clamp(1.0, 6.0), 0.5);
        eq_used = true;
        notes.push(format!(
            "Muffled top end ({:.0} dB below mids) → high shelf +{} dB @ 8 kHz",
            mids - air,
            p.high_shelf_gain_db
        ));
    // Harsh: presence region poking well above the mids → gentle cut.
    } else if presence > mids + 6.0 {
        p.peak3_freq = 4000.0;
        p.peak3_gain_db = -quantize(((presence - mids - 6.0) * 0.5).clamp(1.0, 4.0), 0.5);
        p.peak3_q = 1.2;
        eq_used = true;
        notes.push(format!(
            "Harsh presence region → {} dB @ 4 kHz",
            p.peak3_gain_db
        ));
    }
    // Mud: 250 Hz band well above the rest of the mids.
    let other_mids = (band_db[3] + band_db[4] + band_db[5]) / 3.0;
    if band_db[2] > other_mids + 6.0 {
        p.peak1_freq = 250.0;
        p.peak1_gain_db = -quantize(((band_db[2] - other_mids - 6.0) * 0.5).clamp(1.0, 4.0), 0.5);
        p.peak1_q = 1.0;
        eq_used = true;
        notes.push(format!("Muddy low mids → {} dB @ 250 Hz", p.peak1_gain_db));
    }
    // Thin: bass far below the mids → low-shelf help.
    if bass < mids - 12.0 {
        p.low_shelf_freq = 120.0;
        p.low_shelf_gain_db = quantize(((mids - 12.0 - bass) * 0.4).clamp(1.0, 4.0), 0.5);
        eq_used = true;
        notes.push(format!(
            "Thin low end → low shelf +{} dB @ 120 Hz",
            p.low_shelf_gain_db
        ));
    }
    p.eq_enabled = eq_used;
    if !eq_used {
        notes.push("Tonal balance looks reasonable → EQ left flat".into());
    }

    // ---------- dynamics ----------
    // Spread between loud and audible-quiet windows (ignoring near-noise).
    let audible: Vec<f32> = window_db
        .iter()
        .copied()
        .filter(|d| *d > noise_floor_db + 6.0)
        .collect();
    let spread = match (percentile(&audible, 90.0), percentile(&audible, 20.0)) {
        (Some(hi), Some(lo)) => hi - lo,
        _ => 0.0,
    };
    if spread > 12.0 && audible.len() >= 10 {
        p.comp_enabled = true;
        p.comp_threshold_db = quantize(
            percentile(&audible, 75.0).unwrap_or(-20.0) - 3.0,
            1.0,
        )
        .clamp(-40.0, -6.0);
        p.comp_ratio = if spread > 20.0 { 3.0 } else { 2.0 };
        p.comp_attack_ms = 20.0;
        p.comp_release_ms = 250.0;
        p.comp_makeup_db = 0.0;
        notes.push(format!(
            "Level lurches {spread:.0} dB between sections → compressor {:.0}:1 at {:.0} dB",
            p.comp_ratio, p.comp_threshold_db
        ));
    } else {
        p.comp_enabled = false;
        notes.push(format!(
            "Dynamics are steady ({spread:.0} dB spread) → no compression needed"
        ));
    }

    notes.push(format!(
        "Loudness will be normalized to {:.1} LUFS at export — identical across the album",
        p.target_lufs
    ));

    AutoReport {
        preset: p,
        notes,
        snr_db,
        noise_floor_db,
        dynamic_spread_db: spread,
        band_db: band_db.to_vec(),
    }
}

/// RMS level in dB of each analysis band (single pass, 9 parallel biquads).
fn band_levels(fs: f32, interleaved: &[f32]) -> [f32; 9] {
    let mut filters: Vec<Biquad> = BAND_CENTERS
        .iter()
        .map(|&f| Biquad::with_coeffs(BiquadCoeffs::bandpass(fs, f, 1.0)))
        .collect();
    let mut acc = [0f64; 9];
    let frames = interleaved.len() / 2;
    for f in interleaved.chunks_exact(2) {
        let m = (f[0] + f[1]) * 0.5;
        for (k, filt) in filters.iter_mut().enumerate() {
            let y = filt.process(m);
            acc[k] += (y * y) as f64;
        }
    }
    let mut out = [0f32; 9];
    for k in 0..9 {
        let rms = (acc[k] / frames.max(1) as f64).sqrt() as f32;
        out[k] = 20.0 * rms.max(1e-9).log10();
    }
    out
}

fn percentile(values: &[f32], pct: f32) -> Option<f32> {
    if values.is_empty() {
        return None;
    }
    let mut v = values.to_vec();
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let idx = ((pct / 100.0) * (v.len() - 1) as f32).round() as usize;
    Some(v[idx.min(v.len() - 1)])
}

fn rms(samples: &[f32]) -> f32 {
    if samples.is_empty() {
        return 0.0;
    }
    ((samples.iter().map(|s| (s * s) as f64).sum::<f64>()) / samples.len() as f64).sqrt() as f32
}

fn rms_diff(a: &[f32], b: &[f32]) -> f32 {
    let n = a.len().min(b.len());
    if n == 0 {
        return 0.0;
    }
    ((a[..n]
        .iter()
        .zip(&b[..n])
        .map(|(x, y)| {
            let d = x - y;
            (d * d) as f64
        })
        .sum::<f64>())
        / n as f64)
        .sqrt() as f32
}

fn quantize(v: f32, step: f32) -> f32 {
    (v / step).round() * step
}
