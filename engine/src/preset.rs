use serde::{Deserialize, Serialize};

/// The full processing recipe. Tune it on one track, save it, and batch-apply
/// it to the rest of the album.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct Preset {
    pub version: u32,

    /// AI denoise dry/wet mix, 0.0 (off) .. 1.0 (fully denoised).
    pub denoise_amount: f32,

    pub hpf_enabled: bool,
    /// High-pass (rumble) cutoff in Hz.
    pub hpf_freq: f32,

    pub eq_enabled: bool,
    pub low_shelf_freq: f32,
    pub low_shelf_gain_db: f32,
    pub peak1_freq: f32,
    pub peak1_gain_db: f32,
    pub peak1_q: f32,
    pub peak2_freq: f32,
    pub peak2_gain_db: f32,
    pub peak2_q: f32,
    pub peak3_freq: f32,
    pub peak3_gain_db: f32,
    pub peak3_q: f32,
    pub high_shelf_freq: f32,
    pub high_shelf_gain_db: f32,

    pub comp_enabled: bool,
    pub comp_threshold_db: f32,
    pub comp_ratio: f32,
    pub comp_attack_ms: f32,
    pub comp_release_ms: f32,
    pub comp_makeup_db: f32,

    /// Normalize the track to this integrated loudness (EBU R128).
    pub loudness_enabled: bool,
    pub target_lufs: f32,

    /// Output trim applied on top of loudness normalization.
    pub output_gain_db: f32,

    /// Brick-wall peak limiter ceiling in dBFS (always on, last in chain).
    pub limiter_ceiling_db: f32,

    /// Reference-matching EQ: morph the track's tonal balance toward a
    /// profile built from clean studio tracks. The gains are computed
    /// per-track from the profile (see `profile::match_gains`).
    pub match_enabled: bool,
    pub match_strength: f32,
    pub match_gains: Vec<f32>,
}

impl Default for Preset {
    fn default() -> Self {
        Self {
            version: 1,
            denoise_amount: 0.0,
            hpf_enabled: true,
            hpf_freq: 30.0,
            eq_enabled: false,
            low_shelf_freq: 120.0,
            low_shelf_gain_db: 0.0,
            peak1_freq: 250.0,
            peak1_gain_db: 0.0,
            peak1_q: 1.0,
            peak2_freq: 1000.0,
            peak2_gain_db: 0.0,
            peak2_q: 1.0,
            peak3_freq: 4000.0,
            peak3_gain_db: 0.0,
            peak3_q: 1.0,
            high_shelf_freq: 8000.0,
            high_shelf_gain_db: 0.0,
            comp_enabled: false,
            comp_threshold_db: -18.0,
            comp_ratio: 2.5,
            comp_attack_ms: 15.0,
            comp_release_ms: 150.0,
            comp_makeup_db: 0.0,
            loudness_enabled: true,
            target_lufs: -14.0,
            output_gain_db: 0.0,
            limiter_ceiling_db: -1.0,
            match_enabled: false,
            match_strength: 1.0,
            match_gains: Vec::new(),
        }
    }
}
