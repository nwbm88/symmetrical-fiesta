use crate::AudioData;

/// Min/max peak pairs for drawing a waveform overview.
/// Returns `bins` pairs of (min, max) over the mono mix.
pub fn peaks(audio: &AudioData, bins: usize) -> Vec<(f32, f32)> {
    let frames = audio.frames();
    if frames == 0 || bins == 0 {
        return Vec::new();
    }
    let bins = bins.min(frames);
    let mut out = Vec::with_capacity(bins);
    for b in 0..bins {
        let start = b * frames / bins;
        let end = ((b + 1) * frames / bins).max(start + 1);
        let mut lo = f32::MAX;
        let mut hi = f32::MIN;
        for f in start..end {
            let s = (audio.samples[f * 2] + audio.samples[f * 2 + 1]) * 0.5;
            lo = lo.min(s);
            hi = hi.max(s);
        }
        out.push((lo, hi));
    }
    out
}
