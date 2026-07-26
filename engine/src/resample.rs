use anyhow::Result;
use rubato::{FftFixedIn, Resampler};

use crate::AudioData;

/// High-quality resampling of interleaved stereo audio.
pub fn resample(audio: &AudioData, out_rate: u32) -> Result<AudioData> {
    if audio.sample_rate == out_rate {
        return Ok(audio.clone());
    }
    let in_rate = audio.sample_rate;
    let frames = audio.frames();

    let mut left = Vec::with_capacity(frames);
    let mut right = Vec::with_capacity(frames);
    for f in audio.samples.chunks_exact(2) {
        left.push(f[0]);
        right.push(f[1]);
    }

    const CHUNK: usize = 1024;
    let mut resampler = FftFixedIn::<f32>::new(in_rate as usize, out_rate as usize, CHUNK, 2, 2)?;

    let expected = (frames as u64 * out_rate as u64 / in_rate as u64) as usize;
    let mut out_l: Vec<f32> = Vec::with_capacity(expected + CHUNK * 2);
    let mut out_r: Vec<f32> = Vec::with_capacity(expected + CHUNK * 2);

    let mut pos = 0usize;
    // Feed one extra zero-padded chunk past the end to flush the resampler.
    let mut flushed = false;
    loop {
        let need = resampler.input_frames_next();
        let mut in_l = vec![0f32; need];
        let mut in_r = vec![0f32; need];
        if pos < frames {
            let n = need.min(frames - pos);
            in_l[..n].copy_from_slice(&left[pos..pos + n]);
            in_r[..n].copy_from_slice(&right[pos..pos + n]);
            pos += need;
        } else {
            if flushed {
                break;
            }
            flushed = true;
        }
        let waves = resampler.process(&[in_l, in_r], None)?;
        out_l.extend_from_slice(&waves[0]);
        out_r.extend_from_slice(&waves[1]);
        if out_l.len() >= expected && pos >= frames {
            break;
        }
    }

    out_l.truncate(expected);
    out_r.truncate(expected);
    let mut samples = Vec::with_capacity(out_l.len() * 2);
    for i in 0..out_l.len() {
        samples.push(out_l[i]);
        samples.push(out_r[i]);
    }

    Ok(AudioData {
        sample_rate: out_rate,
        samples,
    })
}
