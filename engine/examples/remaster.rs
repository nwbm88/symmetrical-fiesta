//! Headless smoke test of the full ClearWave pipeline:
//!   cargo run --release -p clearwave-engine --example remaster -- in.mp3 [out.wav]
//! Decodes, runs AI denoise + Auto analysis, renders with the suggested
//! preset and reports before/after measurements.

use std::time::Instant;

use clearwave_engine::{analyze, decode, denoise, loudness, render, resample};

fn main() -> anyhow::Result<()> {
    let mut args = std::env::args().skip(1);
    let input = args.next().expect("usage: remaster <input> [output.wav]");
    let output = args.next().unwrap_or_else(|| "remastered.wav".into());

    let t = Instant::now();
    let native = decode::decode_file(input.as_ref())?;
    println!(
        "decoded: {:.1}s @ {} Hz  ({:.1}s elapsed)",
        native.duration_seconds(),
        native.sample_rate,
        t.elapsed().as_secs_f32()
    );

    let mut dry = resample::resample(&native, 48_000)?;
    drop(native);

    // Test hook: CLEARWAVE_TRIM=<seconds> shortens the input, for quick runs.
    if let Ok(secs) = std::env::var("CLEARWAVE_TRIM") {
        if let Ok(secs) = secs.parse::<f32>() {
            let keep = ((secs * 48_000.0) as usize * 2).min(dry.samples.len());
            dry.samples.truncate(keep);
            println!("trimmed input to {:.1}s", dry.duration_seconds());
        }
    }

    // Test hook: CLEARWAVE_STEMS=<separator cmd> [+ CLEARWAVE_VOICE=<cmd>]
    // exercises the Stem Rescue pipeline on real audio.
    if let Ok(sep) = std::env::var("CLEARWAVE_STEMS") {
        let opts = render::StemRescue {
            separator_cmd: sep,
            voice_cmd: std::env::var("CLEARWAVE_VOICE").ok(),
            vocal_gain_db: std::env::var("CLEARWAVE_VOCAL_DB")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(0.0),
            instrumental_gain_db: std::env::var("CLEARWAVE_INST_DB")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(0.0),
        };
        let t = Instant::now();
        let work = std::env::temp_dir().join("clearwave-example-stems");
        std::fs::create_dir_all(&work)?;
        dry = render::stem_rescue(&dry, &opts, &work)?;
        println!("stem rescue: {:.1}s elapsed", t.elapsed().as_secs_f32());
    }

    // Test hook: CLEARWAVE_ADD_HISS=<amp> mixes white noise into the input,
    // to verify hiss removal end-to-end on real music.
    if let Ok(amp) = std::env::var("CLEARWAVE_ADD_HISS") {
        let amp: f32 = amp.parse().unwrap_or(0.0);
        let mut seed = 0x2468ACE0u32;
        for s in &mut dry.samples {
            seed = seed.wrapping_mul(1664525).wrapping_add(1013904223);
            *s += ((seed as f32 / u32::MAX as f32) * 2.0 - 1.0) * amp;
        }
        println!("added synthetic hiss at amplitude {amp}");
    }

    let t = Instant::now();
    let wet = denoise::denoise(&dry)?;
    println!("AI denoise pass: {:.1}s elapsed", t.elapsed().as_secs_f32());

    let base = clearwave_engine::preset::Preset::default();
    let t = Instant::now();
    let mut report = analyze::auto_preset(48_000, &dry.samples, Some(&wet.samples), &base);

    // Test hook: CLEARWAVE_REF=<file> builds a reference profile from that
    // file and enables matching EQ, demonstrating the profile workflow.
    if let Ok(ref_path) = std::env::var("CLEARWAVE_REF") {
        use clearwave_engine::{dsp, profile};
        let centers = dsp::match_centers();
        let ref_audio = resample::resample(&decode::decode_file(ref_path.as_ref())?, 48_000)?;
        let ref_bands = profile::measure_bands(48_000, &ref_audio.samples, &centers);
        let prof = profile::profile_from_tracks("reference", &[ref_bands])?;
        let track_bands = profile::measure_bands(48_000, &dry.samples, &centers);
        let gains = profile::match_gains(&prof, &track_bands, 1.0)?;
        println!("\nreference profile from {ref_path}");
        for ((c, g), t) in centers.iter().zip(&gains).zip(&track_bands) {
            println!("  {:>6.0} Hz  track {:>6.1} dB  match {:>+5.1} dB", c, t, g);
        }
        report.preset.match_enabled = true;
        report.preset.match_gains = gains;
    }
    println!("auto analysis: {:.1}s elapsed", t.elapsed().as_secs_f32());
    println!("\n=== AUTO REPORT ===");
    println!(
        "noise floor {:.1} dB | SNR {:.1} dB | dynamic spread {:.1} dB",
        report.noise_floor_db, report.snr_db, report.dynamic_spread_db
    );
    for (f, db) in analyze::BAND_CENTERS.iter().zip(&report.band_db) {
        println!("  band {:>6.0} Hz: {:>6.1} dB", f, db);
    }
    for n in &report.notes {
        println!("  • {n}");
    }
    println!("preset: {}", serde_json::to_string_pretty(&report.preset)?);

    let before_lufs = loudness::integrated_lufs(&dry)?;
    let t = Instant::now();
    let out = render::render(&dry, Some(&wet), &report.preset)?;
    let after_lufs = loudness::integrated_lufs(&out)?;
    let peak = out.samples.iter().fold(0f32, |m, s| m.max(s.abs()));
    println!(
        "\nrender: {:.1}s elapsed | LUFS {:.1} -> {:.1} (target {:.1}) | true peak {:.2} dBFS",
        t.elapsed().as_secs_f32(),
        before_lufs,
        after_lufs,
        report.preset.target_lufs,
        20.0 * peak.log10()
    );

    // Noise-floor comparison: quietest 10% of 200 ms windows, before/after,
    // with the loudness gain factored out so the change is denoise+EQ only.
    let gain_db = after_lufs - before_lufs;
    let floor_in = window_floor_db(&dry.samples, dry.sample_rate);
    let floor_out = window_floor_db(&out.samples, out.sample_rate) - gain_db as f32;
    println!(
        "noise floor (gain-compensated): {floor_in:.1} dB -> {floor_out:.1} dB  ({:+.1} dB)",
        floor_out - floor_in
    );

    render::write_wav24(&out, output.as_ref())?;
    println!("wrote {output}");
    Ok(())
}

fn window_floor_db(interleaved: &[f32], rate: u32) -> f32 {
    let win = (rate as usize / 5) * 2; // 200 ms of interleaved samples
    let mut dbs: Vec<f32> = interleaved
        .chunks_exact(win)
        .map(|c| {
            let acc: f64 = c.iter().map(|s| (s * s) as f64).sum();
            10.0 * ((acc / (win / 2) as f64).max(1e-18)).log10() as f32
        })
        .filter(|d| *d > -85.0)
        .collect();
    dbs.sort_by(|a, b| a.partial_cmp(b).unwrap());
    dbs.get(dbs.len() / 10).copied().unwrap_or(-80.0)
}
