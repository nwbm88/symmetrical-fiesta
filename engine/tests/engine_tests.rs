use std::f32::consts::PI;

use clearwave_engine::dsp::DspChain;
use clearwave_engine::preset::Preset;
use clearwave_engine::{decode, denoise, loudness, render, resample, waveform, AudioData};

fn sine(rate: u32, freq: f32, seconds: f32, amp: f32) -> AudioData {
    let frames = (rate as f32 * seconds) as usize;
    let mut samples = Vec::with_capacity(frames * 2);
    for i in 0..frames {
        let s = amp * (2.0 * PI * freq * i as f32 / rate as f32).sin();
        samples.push(s);
        samples.push(s);
    }
    AudioData {
        sample_rate: rate,
        samples,
    }
}

fn white_noise(rate: u32, seconds: f32, amp: f32) -> AudioData {
    let frames = (rate as f32 * seconds) as usize;
    let mut samples = Vec::with_capacity(frames * 2);
    let mut seed = 0x12345678u32;
    let mut next = || {
        seed = seed.wrapping_mul(1664525).wrapping_add(1013904223);
        (seed as f32 / u32::MAX as f32) * 2.0 - 1.0
    };
    for _ in 0..frames {
        samples.push(next() * amp);
        samples.push(next() * amp);
    }
    AudioData {
        sample_rate: rate,
        samples,
    }
}

fn rms(samples: &[f32]) -> f32 {
    (samples.iter().map(|s| s * s).sum::<f32>() / samples.len() as f32).sqrt()
}

#[test]
fn wav_roundtrip_decode() {
    let dir = std::env::temp_dir().join("clearwave_test_roundtrip");
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("tone.wav");

    let audio = sine(44_100, 440.0, 2.0, 0.5);
    render::write_wav24(&audio, &path).unwrap();

    let decoded = decode::decode_file(&path).unwrap();
    assert_eq!(decoded.sample_rate, 44_100);
    assert_eq!(decoded.frames(), audio.frames());
    // 24-bit quantization error is tiny
    let err: f32 = decoded
        .samples
        .iter()
        .zip(audio.samples.iter())
        .map(|(a, b)| (a - b).abs())
        .fold(0.0, f32::max);
    assert!(err < 1e-3, "roundtrip error too large: {err}");
}

#[test]
fn resample_preserves_duration_and_tone() {
    let audio = sine(44_100, 1000.0, 1.0, 0.5);
    let up = resample::resample(&audio, 48_000).unwrap();
    assert_eq!(up.sample_rate, 48_000);
    let expected = 48_000usize;
    assert!(
        (up.frames() as i64 - expected as i64).unsigned_abs() < 16,
        "frames {} vs expected {}",
        up.frames(),
        expected
    );
    // Energy should be roughly preserved (compare interior to avoid edge taper)
    let mid = &up.samples[up.samples.len() / 4..up.samples.len() * 3 / 4];
    let r = rms(mid);
    assert!((r - 0.3535).abs() < 0.05, "rms after resample: {r}");
}

#[test]
fn lufs_measurement_sane() {
    // A 997 Hz stereo sine at -18 dBFS peak measures around -15 LUFS
    // (RMS -21 dB, +3 dB for stereo sum, +~3 dB K-weighting @997Hz ≈ -15..-18).
    let audio = sine(48_000, 997.0, 3.0, 0.125);
    let l = loudness::integrated_lufs(&audio).unwrap();
    assert!(l.is_finite());
    assert!(l < -10.0 && l > -25.0, "unexpected LUFS: {l}");
}

#[test]
fn render_hits_target_lufs() {
    let audio = white_noise(44_100, 3.0, 0.05);
    let preset = Preset {
        loudness_enabled: true,
        target_lufs: -16.0,
        hpf_enabled: false,
        ..Default::default()
    };
    let out = render::render(&audio, None, &preset).unwrap();
    let l = loudness::integrated_lufs(&out).unwrap();
    assert!(
        (l - (-16.0)).abs() < 1.0,
        "rendered loudness {l} not at target -16"
    );
}

#[test]
fn render_with_match_eq_still_hits_target_lufs() {
    // Regression: the limiter pass must not re-apply the matching EQ after
    // loudness measurement (that overshot the target by several dB).
    let audio = white_noise(44_100, 3.0, 0.05);
    let preset = Preset {
        loudness_enabled: true,
        target_lufs: -16.0,
        hpf_enabled: false,
        match_enabled: true,
        match_gains: vec![3.0; clearwave_engine::dsp::MATCH_BANDS],
        ..Default::default()
    };
    let out = render::render(&audio, None, &preset).unwrap();
    let l = loudness::integrated_lufs(&out).unwrap();
    assert!(
        (l - (-16.0)).abs() < 1.0,
        "rendered loudness {l} not at target -16 with match EQ active"
    );
}

#[test]
fn limiter_respects_ceiling() {
    let audio = sine(48_000, 200.0, 1.0, 0.9);
    let preset = Preset {
        loudness_enabled: true,
        target_lufs: -6.0, // push hard into the limiter
        limiter_ceiling_db: -1.0,
        hpf_enabled: false,
        ..Default::default()
    };
    let out = render::render(&audio, None, &preset).unwrap();
    let ceiling = 10f32.powf(-1.0 / 20.0) + 1e-4;
    let peak = out.samples.iter().fold(0f32, |m, s| m.max(s.abs()));
    assert!(peak <= ceiling, "peak {peak} exceeds ceiling {ceiling}");
}

#[test]
fn eq_boost_raises_band_energy() {
    let audio = sine(48_000, 1000.0, 1.0, 0.25);
    let mut chain = DspChain::new(48_000);
    let preset = Preset {
        eq_enabled: true,
        peak2_freq: 1000.0,
        peak2_gain_db: 6.0,
        peak2_q: 1.0,
        hpf_enabled: false,
        comp_enabled: false,
        loudness_enabled: false,
        limiter_ceiling_db: 12.0,
        ..Default::default()
    };
    let mut buf = audio.samples.clone();
    chain.process_block(&mut buf, &preset, 0.0);
    let mid_in = rms(&audio.samples[audio.samples.len() / 2..]);
    let mid_out = rms(&buf[buf.len() / 2..]);
    let gain_db = 20.0 * (mid_out / mid_in).log10();
    assert!(
        (gain_db - 6.0).abs() < 0.5,
        "expected ~6 dB boost, got {gain_db}"
    );
}

#[test]
fn compressor_reduces_dynamics() {
    // Loud and quiet halves; compression should reduce the level difference.
    let mut audio = sine(48_000, 500.0, 2.0, 0.8);
    let half = audio.samples.len() / 2;
    for s in &mut audio.samples[half..] {
        *s *= 0.125; // -18 dB quieter second half
    }
    let mut chain = DspChain::new(48_000);
    let preset = Preset {
        comp_enabled: true,
        comp_threshold_db: -18.0,
        comp_ratio: 4.0,
        comp_attack_ms: 5.0,
        comp_release_ms: 50.0,
        hpf_enabled: false,
        eq_enabled: false,
        loudness_enabled: false,
        limiter_ceiling_db: 12.0,
        ..Default::default()
    };
    let mut buf = audio.samples.clone();
    chain.process_block(&mut buf, &preset, 0.0);

    let loud_in = rms(&audio.samples[half / 2..half]);
    let quiet_in = rms(&audio.samples[half + half / 2..]);
    let loud_out = rms(&buf[half / 2..half]);
    let quiet_out = rms(&buf[half + half / 2..]);
    let range_in = 20.0 * (loud_in / quiet_in).log10();
    let range_out = 20.0 * (loud_out / quiet_out).log10();
    assert!(
        range_out < range_in - 3.0,
        "dynamic range not reduced: {range_in} -> {range_out}"
    );
}

#[test]
fn ai_denoise_reduces_noise_but_keeps_length() {
    // Pure hiss: RNNoise should attenuate it substantially.
    let noisy = white_noise(48_000, 2.0, 0.05);
    let cleaned = denoise::denoise(&noisy).unwrap();
    assert_eq!(cleaned.samples.len(), noisy.samples.len());
    assert_eq!(cleaned.sample_rate, noisy.sample_rate);
    let tail = noisy.samples.len() / 2;
    let in_rms = rms(&noisy.samples[tail..]);
    let out_rms = rms(&cleaned.samples[tail..]);
    assert!(
        out_rms < in_rms * 0.7,
        "denoise did not reduce hiss: {in_rms} -> {out_rms}"
    );
}

#[test]
fn preset_json_roundtrip() {
    let p = Preset {
        denoise_amount: 0.6,
        target_lufs: -15.5,
        ..Default::default()
    };
    let json = serde_json::to_string_pretty(&p).unwrap();
    let back: Preset = serde_json::from_str(&json).unwrap();
    assert_eq!(p, back);
    // Older/partial presets must still load thanks to serde(default).
    let partial: Preset = serde_json::from_str(r#"{"denoise_amount": 0.25}"#).unwrap();
    assert!((partial.denoise_amount - 0.25).abs() < 1e-6);
}

#[test]
fn auto_mode_detects_hiss() {
    // Music-like tone bursts over a constant hiss bed.
    let rate = 48_000u32;
    let mut audio = white_noise(rate, 8.0, 0.01); // hiss at ~-40 dB
    let frames = audio.frames();
    for i in 0..frames {
        // 1 s on / 1 s off tone bursts to create quiet windows
        let sec = i / rate as usize;
        if sec % 2 == 0 {
            let s = 0.4 * (2.0 * PI * 440.0 * i as f32 / rate as f32).sin();
            audio.samples[i * 2] += s;
            audio.samples[i * 2 + 1] += s;
        }
    }
    let wet = denoise::denoise(&audio).unwrap();
    let report = clearwave_engine::analyze::auto_preset(
        rate,
        &audio.samples,
        Some(&wet.samples),
        &Preset::default(),
    );
    assert!(
        report.preset.denoise_amount >= 0.2,
        "hissy track should get denoise, got {}",
        report.preset.denoise_amount
    );
    assert!(!report.notes.is_empty());
}

#[test]
fn auto_mode_leaves_clean_audio_alone() {
    // Clean tone bursts over a barely-there -70 dB room tone: the noise
    // floor is inaudible, so auto mode should keep denoise near zero.
    let rate = 48_000u32;
    let mut audio = white_noise(rate, 8.0, 0.0003);
    let frames = audio.frames();
    for i in 0..frames {
        let sec = i / rate as usize;
        if sec % 2 == 0 {
            let s = 0.4 * (2.0 * PI * 440.0 * i as f32 / rate as f32).sin();
            audio.samples[i * 2] += s;
            audio.samples[i * 2 + 1] += s;
        }
    }
    let wet = denoise::denoise(&audio).unwrap();
    let report = clearwave_engine::analyze::auto_preset(
        rate,
        &audio.samples,
        Some(&wet.samples),
        &Preset::default(),
    );
    assert!(
        report.preset.denoise_amount <= 0.2,
        "clean track should get little/no denoise, got {}",
        report.preset.denoise_amount
    );
}

#[test]
fn auto_mode_detects_muffled_audio() {
    // White noise low-passed hard at 1 kHz = "muffled" spectrum.
    let rate = 48_000u32;
    let mut audio = white_noise(rate, 6.0, 0.2);
    let coeffs = clearwave_engine::dsp::BiquadCoeffs::lowpass(rate as f32, 1000.0, 0.707);
    for _ in 0..2 {
        let mut fl = clearwave_engine::dsp::Biquad::with_coeffs(coeffs);
        let mut fr = clearwave_engine::dsp::Biquad::with_coeffs(coeffs);
        for f in audio.samples.chunks_exact_mut(2) {
            f[0] = fl.process(f[0]);
            f[1] = fr.process(f[1]);
        }
    }
    let report = clearwave_engine::analyze::auto_preset(
        rate,
        &audio.samples,
        None,
        &Preset::default(),
    );
    assert!(report.preset.eq_enabled, "muffled track should enable EQ");
    assert!(
        report.preset.high_shelf_gain_db >= 1.0,
        "muffled track should get a high-shelf boost, got {}",
        report.preset.high_shelf_gain_db
    );
}

#[test]
fn auto_mode_detects_wild_dynamics() {
    // Realistic shape: loud passages, much quieter passages, and true
    // near-silent gaps (room tone) that establish the noise floor.
    let rate = 48_000u32;
    let mut audio = white_noise(rate, 16.0, 0.0005); // -66 dB bed
    let frames = audio.frames();
    for i in 0..frames {
        let sec = i / rate as usize;
        let amp = match sec % 4 {
            0 | 1 => 0.7,  // loud section
            2 => 0.035,    // quiet section (~-26 dB below loud)
            _ => 0.0,      // gap: bed only
        };
        if amp > 0.0 {
            let s = amp * (2.0 * PI * 300.0 * i as f32 / rate as f32).sin();
            audio.samples[i * 2] += s;
            audio.samples[i * 2 + 1] += s;
        }
    }
    let report = clearwave_engine::analyze::auto_preset(
        rate,
        &audio.samples,
        None,
        &Preset::default(),
    );
    assert!(
        report.preset.comp_enabled,
        "wildly dynamic track should enable the compressor (spread {})",
        report.dynamic_spread_db
    );
}

#[test]
fn profile_roundtrip_and_matching_moves_spectrum_toward_reference() {
    use clearwave_engine::dsp::{match_centers, DspChain};
    use clearwave_engine::profile;

    let rate = 48_000u32;
    let centers = match_centers();

    // Reference: full-bandwidth noise. Track: same noise low-passed at 2 kHz
    // (i.e. a muffled recording of the same "sound").
    let reference = white_noise(rate, 6.0, 0.1);
    let mut track = reference.clone();
    let lp = clearwave_engine::dsp::BiquadCoeffs::lowpass(rate as f32, 2000.0, 0.707);
    let mut fl = clearwave_engine::dsp::Biquad::with_coeffs(lp);
    let mut fr = clearwave_engine::dsp::Biquad::with_coeffs(lp);
    for f in track.samples.chunks_exact_mut(2) {
        f[0] = fl.process(f[0]);
        f[1] = fr.process(f[1]);
    }

    let ref_bands = profile::measure_bands(rate, &reference.samples, &centers);
    let prof = profile::profile_from_tracks("test", &[ref_bands.clone()]).unwrap();

    // JSON roundtrip
    let back: profile::Profile =
        serde_json::from_str(&serde_json::to_string(&prof).unwrap()).unwrap();
    assert_eq!(prof, back);

    let track_bands = profile::measure_bands(rate, &track.samples, &centers);
    let gains = profile::match_gains(&prof, &track_bands, 1.0).unwrap();
    // High bands must get boosted, low bands mostly untouched.
    assert!(gains[20] > 3.0, "expected top-band boost, got {}", gains[20]);
    assert!(gains[2].abs() < 3.0, "low bands should be ~flat, got {}", gains[2]);

    // Apply the matching EQ and verify the spectral distance shrinks.
    let preset = Preset {
        match_enabled: true,
        match_gains: gains,
        hpf_enabled: false,
        eq_enabled: false,
        comp_enabled: false,
        loudness_enabled: false,
        limiter_ceiling_db: 12.0,
        ..Default::default()
    };
    let mut chain = DspChain::new(rate);
    let mut buf = track.samples.clone();
    for block in buf.chunks_mut(8192) {
        chain.process_block(block, &preset, 0.0);
    }
    let matched_bands = profile::measure_bands(rate, &buf, &centers);

    let dist = |bands: &[f32]| -> f32 {
        let n = profile::normalize_bands(&centers, bands);
        n.iter()
            .zip(&prof.band_db)
            .map(|(a, b)| (a - b).abs())
            .sum::<f32>()
            / n.len() as f32
    };
    let before = dist(&track_bands);
    let after = dist(&matched_bands);
    assert!(
        after < before * 0.6,
        "matching should close most of the spectral gap: {before:.2} -> {after:.2} dB avg"
    );
}

#[cfg(unix)]
#[test]
fn external_preprocess_stub_tool_with_pick() {
    let audio = sine(48_000, 500.0, 1.0, 0.3);
    let work = std::env::temp_dir().join("clearwave_test_ext");
    let _ = std::fs::remove_dir_all(&work);

    // Stub "AI tool": copies input into the out dir under stem-style names;
    // the pick must select the No_Crowd one.
    let cmd = r#"sh -c "cp {in} {outdir}/result_No_Crowd.wav && cp {in} {outdir}/result_Crowd.wav""#;
    let out = clearwave_engine::render::external_preprocess(&audio, cmd, &work, Some("no_crowd"))
        .unwrap();
    assert_eq!(out.samples.len(), audio.samples.len());
    // Content survives the WAV roundtrip
    let err: f32 = out
        .samples
        .iter()
        .zip(&audio.samples)
        .map(|(a, b)| (a - b).abs())
        .fold(0.0, f32::max);
    assert!(err < 1e-3, "roundtrip error {err}");
}

#[test]
fn waveform_peaks_shape() {
    let audio = sine(44_100, 440.0, 1.0, 0.5);
    let peaks = waveform::peaks(&audio, 500);
    assert_eq!(peaks.len(), 500);
    assert!(peaks.iter().all(|(lo, hi)| lo <= hi));
    assert!(peaks.iter().any(|(_, hi)| *hi > 0.4));
}
