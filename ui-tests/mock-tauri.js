/* Mock Tauri bridge so the real ClearWave UI can be rendered in a browser
   for screenshots. Serves plausible data shaped exactly like the Rust
   commands return. */
(function () {
  const S = {
    playing: true,
    pos: 78.4,
    dur: 212.0,
    hasTrack: false,
    peakL: 0.62,
    peakR: 0.58,
    gr: 3.4,
  };
  window.__mockState = S;

  function waveform(n) {
    const mn = [], mx = [];
    let seed = 12345;
    const rnd = () => {
      seed = (seed * 1664525 + 1013904223) % 4294967296;
      return seed / 4294967296;
    };
    for (let i = 0; i < n; i++) {
      const t = i / n;
      // song-like envelope: intro, verses, chorus peaks, outro
      const env =
        0.32 +
        0.42 * Math.sin(Math.PI * Math.min(1, t * 1.08)) +
        0.16 * Math.sin(t * 34) +
        0.1 * Math.sin(t * 97);
      const a = Math.max(0.05, Math.min(0.97, env * (0.62 + 0.5 * rnd())));
      mx.push(a);
      mn.push(-a * (0.85 + 0.15 * rnd()));
    }
    return { mn, mx };
  }

  const AUTO_NOTES = [
    "Noise floor −32 dB (SNR 13 dB) → AI denoise 55%",
    "Strong low-frequency rumble → high-pass at 60 Hz",
    "Muffled top end (16 dB below mids) → high shelf +2.5 dB @ 8 kHz",
    "Muddy low mids → −2.0 dB @ 250 Hz",
    "Level lurches 14 dB between sections → compressor 2:1 at −21 dB",
    "Loudness will be normalized to −14.0 LUFS at export — identical across the album",
  ];

  const AUTO_PRESET = {
    version: 1,
    denoise_amount: 0.55,
    hpf_enabled: true, hpf_freq: 60,
    eq_enabled: true,
    low_shelf_freq: 120, low_shelf_gain_db: -1.5,
    peak1_freq: 250, peak1_gain_db: -2.0, peak1_q: 1,
    peak2_freq: 1000, peak2_gain_db: 0, peak2_q: 1,
    peak3_freq: 4000, peak3_gain_db: 0, peak3_q: 1,
    high_shelf_freq: 8000, high_shelf_gain_db: 2.5,
    comp_enabled: true, comp_threshold_db: -21, comp_ratio: 2,
    comp_attack_ms: 20, comp_release_ms: 250, comp_makeup_db: 0,
    loudness_enabled: true, target_lufs: -14,
    output_gain_db: 0, limiter_ceiling_db: -1,
    match_enabled: false, match_strength: 1, match_gains: [],
  };

  window.__TAURI__ = {
    core: {
      async invoke(cmd, args) {
        switch (cmd) {
          case "get_status":
            if (S.playing) S.pos = (S.pos + 0.1) % S.dur;
            return {
              has_track: S.hasTrack,
              playing: S.playing,
              position_seconds: S.pos,
              duration_seconds: S.hasTrack ? S.dur : 0,
              peak_l: S.hasTrack ? S.peakL : 0,
              peak_r: S.hasTrack ? S.peakR : 0,
              gain_reduction_db: S.hasTrack ? S.gr : 0,
              bypass: false,
              batch_running: false,
            };
          case "load_track": {
            const w = waveform(1200);
            S.hasTrack = true;
            return {
              file_name: window.__mockTrackName || "03 - Riverside Blues (live).mp3",
              path: args.path,
              duration_seconds: S.dur,
              engine_rate: 48000,
              source_rate: 44100,
              lufs_dry: -18.8,
              lufs_wet: -19.2,
              waveform_min: w.mn,
              waveform_max: w.mx,
              playback_ok: true,
            };
          }
          case "auto_settings":
            return { preset: AUTO_PRESET, notes: AUTO_NOTES, snr_db: 12.5,
                     noise_floor_db: -32.3, dynamic_spread_db: 14.1, band_db: [] };
          case "build_profile":
            return { version: 1, name: args.name, num_tracks: args.files.length,
                     band_db: new Array(24).fill(0) };
          case "load_profile":
            return { version: 1, name: "Dad's band — 1978 studio", num_tracks: 4,
                     band_db: new Array(24).fill(0) };
          case "match_gains_current":
            return new Array(24).fill(0).map((_, i) => Math.sin(i / 3) * 2.5);
          case "detect_tools":
            return { ffmpeg: true, deepfilter: true, demucs: false, uvr: true };
          case "export_track":
            return "Exported to C:\\Users\\you\\Music\\Remastered\\track.wav";
          default:
            return null;
        }
      },
    },
    event: {
      async listen(name, cb) {
        window.__emit = (payload) => cb({ payload });
        return () => {};
      },
    },
    dialog: {
      async open(opts) {
        if (opts && opts.directory) return "C:\\Users\\you\\Music\\Remastered";
        if (window.__mockPick) return window.__mockPick;
        return null;
      },
      async save() { return null; },
    },
  };
})();
