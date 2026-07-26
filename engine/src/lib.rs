//! ClearWave audio engine: decoding, restoration DSP, AI denoising,
//! loudness normalization, offline rendering and real-time playback.

pub mod analyze;
pub mod decode;
pub mod denoise;
pub mod dsp;
pub mod loudness;
pub mod preset;
pub mod profile;
pub mod render;
pub mod resample;
pub mod waveform;

#[cfg(feature = "playback")]
pub mod player;

/// Interleaved stereo f32 audio at a known sample rate.
#[derive(Clone, Debug, Default)]
pub struct AudioData {
    pub sample_rate: u32,
    /// Interleaved stereo samples: [L0, R0, L1, R1, ...]
    pub samples: Vec<f32>,
}

impl AudioData {
    pub fn frames(&self) -> usize {
        self.samples.len() / 2
    }

    pub fn duration_seconds(&self) -> f64 {
        self.frames() as f64 / self.sample_rate as f64
    }
}
