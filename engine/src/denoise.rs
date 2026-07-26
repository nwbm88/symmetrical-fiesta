//! Local AI noise reduction using the RNNoise recurrent neural network
//! (via the pure-Rust `nnnoiseless` port). Runs entirely offline on the
//! user's machine — no audio ever leaves the computer.

use anyhow::Result;
use nnnoiseless::DenoiseState;

use crate::{resample, AudioData};

/// RNNoise operates on 48 kHz audio in frames of 480 samples.
pub const RNNOISE_RATE: u32 = 48_000;

/// Denoise interleaved stereo audio. The input is resampled to 48 kHz for
/// the network and resampled back, so any engine rate is accepted.
/// Returns a buffer with the same sample rate and frame count as the input.
pub fn denoise(audio: &AudioData) -> Result<AudioData> {
    let at_48k = resample::resample(audio, RNNOISE_RATE)?;
    let denoised_48k = denoise_stereo_48k(&at_48k.samples);
    let mut back = resample::resample(
        &AudioData {
            sample_rate: RNNOISE_RATE,
            samples: denoised_48k,
        },
        audio.sample_rate,
    )?;
    // Keep dry/wet buffers exactly the same length so they can be crossfaded.
    back.samples.resize(audio.samples.len(), 0.0);
    back.sample_rate = audio.sample_rate;
    Ok(back)
}

fn denoise_stereo_48k(interleaved: &[f32]) -> Vec<f32> {
    const FRAME: usize = DenoiseState::FRAME_SIZE;
    let frames = interleaved.len() / 2;

    let mut left = vec![0f32; frames];
    let mut right = vec![0f32; frames];
    for (i, f) in interleaved.chunks_exact(2).enumerate() {
        left[i] = f[0];
        right[i] = f[1];
    }

    let out_l = denoise_channel_48k(&left, FRAME);
    let out_r = denoise_channel_48k(&right, FRAME);

    let mut out = vec![0f32; interleaved.len()];
    for i in 0..frames {
        out[i * 2] = out_l[i];
        out[i * 2 + 1] = out_r[i];
    }
    out
}

fn denoise_channel_48k(samples: &[f32], frame: usize) -> Vec<f32> {
    let mut state = DenoiseState::new();
    let mut out = Vec::with_capacity(samples.len() + frame);
    let mut in_buf = vec![0f32; frame];
    let mut out_buf = vec![0f32; frame];

    for chunk in samples.chunks(frame) {
        in_buf[..chunk.len()].copy_from_slice(chunk);
        for s in &mut in_buf[chunk.len()..] {
            *s = 0.0;
        }
        // nnnoiseless expects samples in i16 full-scale range.
        for s in &mut in_buf {
            *s *= 32768.0;
        }
        state.process_frame(&mut out_buf, &in_buf);
        out.extend(out_buf.iter().map(|&s| s / 32768.0));
    }

    out.truncate(samples.len());
    out.resize(samples.len(), 0.0);
    out
}
