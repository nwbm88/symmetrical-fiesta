//! Real-time-safe DSP: RBJ biquad filters, a soft-knee stereo compressor
//! and a brick-wall peak limiter, assembled into the ClearWave chain:
//!
//!   [dry/wet AI denoise mix] -> HPF -> 5-band EQ -> Compressor
//!       -> gain (loudness + trim) -> Limiter

use crate::preset::Preset;

const DENORM_GUARD: f32 = 1e-15;

#[derive(Clone, Copy, Debug, Default)]
pub struct BiquadCoeffs {
    pub b0: f32,
    pub b1: f32,
    pub b2: f32,
    pub a1: f32,
    pub a2: f32,
}

impl BiquadCoeffs {
    pub fn identity() -> Self {
        Self {
            b0: 1.0,
            ..Default::default()
        }
    }

    pub fn highpass(fs: f32, freq: f32, q: f32) -> Self {
        let freq = freq.clamp(10.0, fs * 0.45);
        let w0 = 2.0 * std::f32::consts::PI * freq / fs;
        let (sin, cos) = w0.sin_cos();
        let alpha = sin / (2.0 * q);
        let a0 = 1.0 + alpha;
        Self {
            b0: (1.0 + cos) / 2.0 / a0,
            b1: -(1.0 + cos) / a0,
            b2: (1.0 + cos) / 2.0 / a0,
            a1: -2.0 * cos / a0,
            a2: (1.0 - alpha) / a0,
        }
    }

    pub fn peaking(fs: f32, freq: f32, gain_db: f32, q: f32) -> Self {
        if gain_db.abs() < 0.01 {
            return Self::identity();
        }
        let freq = freq.clamp(20.0, fs * 0.45);
        let a = 10f32.powf(gain_db / 40.0);
        let w0 = 2.0 * std::f32::consts::PI * freq / fs;
        let (sin, cos) = w0.sin_cos();
        let alpha = sin / (2.0 * q.max(0.1));
        let a0 = 1.0 + alpha / a;
        Self {
            b0: (1.0 + alpha * a) / a0,
            b1: -2.0 * cos / a0,
            b2: (1.0 - alpha * a) / a0,
            a1: -2.0 * cos / a0,
            a2: (1.0 - alpha / a) / a0,
        }
    }

    pub fn low_shelf(fs: f32, freq: f32, gain_db: f32) -> Self {
        if gain_db.abs() < 0.01 {
            return Self::identity();
        }
        let freq = freq.clamp(20.0, fs * 0.45);
        let a = 10f32.powf(gain_db / 40.0);
        let w0 = 2.0 * std::f32::consts::PI * freq / fs;
        let (sin, cos) = w0.sin_cos();
        let alpha = sin / 2.0 * (2.0f32).sqrt();
        let two_sqrt_a_alpha = 2.0 * a.sqrt() * alpha;
        let a0 = (a + 1.0) + (a - 1.0) * cos + two_sqrt_a_alpha;
        Self {
            b0: a * ((a + 1.0) - (a - 1.0) * cos + two_sqrt_a_alpha) / a0,
            b1: 2.0 * a * ((a - 1.0) - (a + 1.0) * cos) / a0,
            b2: a * ((a + 1.0) - (a - 1.0) * cos - two_sqrt_a_alpha) / a0,
            a1: -2.0 * ((a - 1.0) + (a + 1.0) * cos) / a0,
            a2: ((a + 1.0) + (a - 1.0) * cos - two_sqrt_a_alpha) / a0,
        }
    }

    pub fn high_shelf(fs: f32, freq: f32, gain_db: f32) -> Self {
        if gain_db.abs() < 0.01 {
            return Self::identity();
        }
        let freq = freq.clamp(20.0, fs * 0.45);
        let a = 10f32.powf(gain_db / 40.0);
        let w0 = 2.0 * std::f32::consts::PI * freq / fs;
        let (sin, cos) = w0.sin_cos();
        let alpha = sin / 2.0 * (2.0f32).sqrt();
        let two_sqrt_a_alpha = 2.0 * a.sqrt() * alpha;
        let a0 = (a + 1.0) - (a - 1.0) * cos + two_sqrt_a_alpha;
        Self {
            b0: a * ((a + 1.0) + (a - 1.0) * cos + two_sqrt_a_alpha) / a0,
            b1: -2.0 * a * ((a - 1.0) + (a + 1.0) * cos) / a0,
            b2: a * ((a + 1.0) + (a - 1.0) * cos - two_sqrt_a_alpha) / a0,
            a1: 2.0 * ((a - 1.0) - (a + 1.0) * cos) / a0,
            a2: ((a + 1.0) - (a - 1.0) * cos - two_sqrt_a_alpha) / a0,
        }
    }
}

/// Transposed direct form II biquad with per-channel state.
#[derive(Clone, Copy, Debug, Default)]
pub struct Biquad {
    pub c: BiquadCoeffs,
    z1: f32,
    z2: f32,
}

impl Biquad {
    #[inline]
    pub fn process(&mut self, x: f32) -> f32 {
        let y = self.c.b0 * x + self.z1;
        self.z1 = self.c.b1 * x - self.c.a1 * y + self.z2;
        self.z2 = self.c.b2 * x - self.c.a2 * y + DENORM_GUARD;
        y
    }

    pub fn reset(&mut self) {
        self.z1 = 0.0;
        self.z2 = 0.0;
    }
}

/// Stereo-linked soft-knee downward compressor with log-domain envelope.
#[derive(Clone, Debug, Default)]
pub struct Compressor {
    env_db: f32,
}

impl Compressor {
    /// Returns current gain reduction in dB (positive number = reducing).
    #[inline]
    pub fn process_frame(
        &mut self,
        l: &mut f32,
        r: &mut f32,
        fs: f32,
        threshold_db: f32,
        ratio: f32,
        attack_ms: f32,
        release_ms: f32,
        makeup_db: f32,
    ) -> f32 {
        const KNEE_DB: f32 = 6.0;
        let level = l.abs().max(r.abs()).max(1e-6);
        let level_db = 20.0 * level.log10();
        let over = level_db - threshold_db;

        let slope = 1.0 - 1.0 / ratio.max(1.0);
        let desired_gr = if over <= -KNEE_DB / 2.0 {
            0.0
        } else if over < KNEE_DB / 2.0 {
            let t = over + KNEE_DB / 2.0;
            slope * t * t / (2.0 * KNEE_DB)
        } else {
            slope * over
        };

        let attack = coef(fs, attack_ms);
        let release = coef(fs, release_ms);
        if desired_gr > self.env_db {
            self.env_db = attack * self.env_db + (1.0 - attack) * desired_gr;
        } else {
            self.env_db = release * self.env_db + (1.0 - release) * desired_gr;
        }

        let gain = 10f32.powf((makeup_db - self.env_db) / 20.0);
        *l *= gain;
        *r *= gain;
        self.env_db
    }

    pub fn reset(&mut self) {
        self.env_db = 0.0;
    }
}

#[inline]
fn coef(fs: f32, ms: f32) -> f32 {
    (-1.0 / (fs * (ms.max(0.02) / 1000.0))).exp()
}

/// Brick-wall peak limiter: instant attack, smoothed release, hard safety clamp.
#[derive(Clone, Debug)]
pub struct Limiter {
    gain: f32,
}

impl Default for Limiter {
    fn default() -> Self {
        Self { gain: 1.0 }
    }
}

impl Limiter {
    #[inline]
    pub fn process_frame(&mut self, l: &mut f32, r: &mut f32, fs: f32, ceiling_db: f32) -> f32 {
        let ceiling = 10f32.powf(ceiling_db / 20.0);
        let peak = l.abs().max(r.abs());
        let needed = if peak * self.gain > ceiling {
            ceiling / peak.max(1e-9)
        } else {
            1.0
        };
        if needed < self.gain {
            self.gain = needed; // instant attack
        } else {
            let release = coef(fs, 80.0);
            self.gain = release * self.gain + (1.0 - release) * needed.min(1.0);
        }
        *l = (*l * self.gain).clamp(-ceiling, ceiling);
        *r = (*r * self.gain).clamp(-ceiling, ceiling);
        self.gain
    }

    pub fn reset(&mut self) {
        self.gain = 1.0;
    }
}

/// Number of biquad stages per channel: 2x HPF (24 dB/oct) + 5 EQ bands.
const HPF_STAGES: usize = 2;
const EQ_BANDS: usize = 5;

/// Block metering snapshot produced while processing.
#[derive(Clone, Copy, Debug, Default)]
pub struct BlockMeters {
    pub peak_l: f32,
    pub peak_r: f32,
    pub gain_reduction_db: f32,
    pub limiter_gain: f32,
}

/// The complete stateful processing chain for one stereo stream.
pub struct DspChain {
    fs: f32,
    hpf: [[Biquad; HPF_STAGES]; 2],
    eq: [[Biquad; EQ_BANDS]; 2],
    comp: Compressor,
    limiter: Limiter,
}

impl DspChain {
    pub fn new(sample_rate: u32) -> Self {
        Self {
            fs: sample_rate as f32,
            hpf: Default::default(),
            eq: Default::default(),
            comp: Compressor::default(),
            limiter: Limiter::default(),
        }
    }

    pub fn reset(&mut self) {
        for ch in &mut self.hpf {
            for b in ch.iter_mut() {
                b.reset();
            }
        }
        for ch in &mut self.eq {
            for b in ch.iter_mut() {
                b.reset();
            }
        }
        self.comp.reset();
        self.limiter.reset();
    }

    fn update_coeffs(&mut self, p: &Preset) {
        let hpf_c = BiquadCoeffs::highpass(self.fs, p.hpf_freq, std::f32::consts::FRAC_1_SQRT_2);
        let eq_c = [
            BiquadCoeffs::low_shelf(self.fs, p.low_shelf_freq, p.low_shelf_gain_db),
            BiquadCoeffs::peaking(self.fs, p.peak1_freq, p.peak1_gain_db, p.peak1_q),
            BiquadCoeffs::peaking(self.fs, p.peak2_freq, p.peak2_gain_db, p.peak2_q),
            BiquadCoeffs::peaking(self.fs, p.peak3_freq, p.peak3_gain_db, p.peak3_q),
            BiquadCoeffs::high_shelf(self.fs, p.high_shelf_freq, p.high_shelf_gain_db),
        ];
        for ch in 0..2 {
            for s in 0..HPF_STAGES {
                self.hpf[ch][s].c = hpf_c;
            }
            for (s, c) in eq_c.iter().enumerate() {
                self.eq[ch][s].c = *c;
            }
        }
    }

    /// Process an interleaved stereo block in place.
    ///
    /// `auto_gain_db` is the loudness-normalization gain computed by the
    /// caller (target LUFS minus measured LUFS); it is applied together with
    /// the preset's output trim, before the limiter.
    pub fn process_block(&mut self, buf: &mut [f32], p: &Preset, auto_gain_db: f32) -> BlockMeters {
        self.update_coeffs(p);

        let mut gain_db = p.output_gain_db;
        if p.loudness_enabled {
            gain_db += auto_gain_db;
        }
        let out_gain = 10f32.powf(gain_db / 20.0);

        let mut m = BlockMeters {
            limiter_gain: 1.0,
            ..Default::default()
        };

        for frame in buf.chunks_exact_mut(2) {
            let mut l = frame[0];
            let mut r = frame[1];

            if p.hpf_enabled {
                for s in 0..HPF_STAGES {
                    l = self.hpf[0][s].process(l);
                    r = self.hpf[1][s].process(r);
                }
            }
            if p.eq_enabled {
                for s in 0..EQ_BANDS {
                    l = self.eq[0][s].process(l);
                    r = self.eq[1][s].process(r);
                }
            }
            if p.comp_enabled {
                let gr = self.comp.process_frame(
                    &mut l,
                    &mut r,
                    self.fs,
                    p.comp_threshold_db,
                    p.comp_ratio,
                    p.comp_attack_ms,
                    p.comp_release_ms,
                    p.comp_makeup_db,
                );
                m.gain_reduction_db = m.gain_reduction_db.max(gr);
            }

            l *= out_gain;
            r *= out_gain;

            let lim = self
                .limiter
                .process_frame(&mut l, &mut r, self.fs, p.limiter_ceiling_db);
            m.limiter_gain = m.limiter_gain.min(lim);

            m.peak_l = m.peak_l.max(l.abs());
            m.peak_r = m.peak_r.max(r.abs());
            frame[0] = l;
            frame[1] = r;
        }
        m
    }
}

/// Crossfade dry and AI-denoised buffers into `out`.
pub fn mix_denoise(dry: &[f32], wet: &[f32], amount: f32, out: &mut [f32]) {
    let amount = amount.clamp(0.0, 1.0);
    if amount <= 0.0 || wet.is_empty() {
        out.copy_from_slice(&dry[..out.len()]);
        return;
    }
    let dry_g = 1.0 - amount;
    for i in 0..out.len() {
        out[i] = dry[i] * dry_g + wet[i] * amount;
    }
}
