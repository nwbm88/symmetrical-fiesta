//! Real-time playback engine. A dedicated thread owns the cpal output
//! stream; the UI thread controls playback through lock-free shared state,
//! so slider moves are heard instantly without clicks or blocking.

use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, RwLock};

use anyhow::{anyhow, Result};
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};

use crate::dsp::{mix_denoise, DspChain};
use crate::preset::Preset;

/// The decoded track, resampled to the output device rate, with the
/// AI-denoised twin used for live dry/wet crossfading.
pub struct TrackBuffers {
    pub dry: Vec<f32>,
    pub wet: Vec<f32>,
    pub frames: usize,
}

#[derive(Default)]
pub struct Meters {
    pub peak_l: AtomicU32,
    pub peak_r: AtomicU32,
    pub gain_reduction_db: AtomicU32,
}

pub struct SharedState {
    pub track: RwLock<Option<Arc<TrackBuffers>>>,
    pub position: AtomicUsize,
    pub playing: AtomicBool,
    pub bypass: AtomicBool,
    pub preset: Mutex<Preset>,
    pub preset_version: AtomicU64,
    /// Loudness normalization gain for live preview (f32 bits).
    pub auto_gain_db: AtomicU32,
    pub meters: Meters,
}

impl Default for SharedState {
    fn default() -> Self {
        Self {
            track: RwLock::new(None),
            position: AtomicUsize::new(0),
            playing: AtomicBool::new(false),
            bypass: AtomicBool::new(false),
            preset: Mutex::new(Preset::default()),
            preset_version: AtomicU64::new(1),
            auto_gain_db: AtomicU32::new(0f32.to_bits()),
            meters: Meters::default(),
        }
    }
}

impl SharedState {
    pub fn set_preset(&self, p: Preset) {
        *self.preset.lock().unwrap() = p;
        self.preset_version.fetch_add(1, Ordering::Release);
    }

    pub fn set_auto_gain_db(&self, db: f32) {
        self.auto_gain_db.store(db.to_bits(), Ordering::Relaxed);
    }
}

pub fn load_f32(a: &AtomicU32) -> f32 {
    f32::from_bits(a.load(Ordering::Relaxed))
}

fn store_f32(a: &AtomicU32, v: f32) {
    a.store(v.to_bits(), Ordering::Relaxed);
}

/// Owns the audio output. Dropping it stops playback.
pub struct Player {
    pub sample_rate: u32,
    _keepalive: std::sync::mpsc::Sender<()>,
}

impl Player {
    /// Start the output stream on a dedicated thread. Returns once the
    /// device is up, reporting the device sample rate used by the engine.
    pub fn start(shared: Arc<SharedState>) -> Result<Player> {
        let (init_tx, init_rx) = std::sync::mpsc::channel::<Result<u32>>();
        let (keep_tx, keep_rx) = std::sync::mpsc::channel::<()>();

        std::thread::Builder::new()
            .name("clearwave-audio".into())
            .spawn(move || {
                let stream = match build_stream(shared) {
                    Ok((stream, rate)) => {
                        let _ = init_tx.send(Ok(rate));
                        stream
                    }
                    Err(e) => {
                        let _ = init_tx.send(Err(e));
                        return;
                    }
                };
                if let Err(e) = stream.play() {
                    eprintln!("audio stream error: {e}");
                }
                // Keep the stream alive until the Player is dropped.
                let _ = keep_rx.recv();
                drop(stream);
            })?;

        let sample_rate = init_rx
            .recv()
            .map_err(|_| anyhow!("audio thread died during init"))??;
        Ok(Player {
            sample_rate,
            _keepalive: keep_tx,
        })
    }
}

fn build_stream(shared: Arc<SharedState>) -> Result<(cpal::Stream, u32)> {
    let host = cpal::default_host();
    let device = host
        .default_output_device()
        .ok_or_else(|| anyhow!("no audio output device found"))?;
    let config = device.default_output_config()?;
    let sample_rate = config.sample_rate().0;
    let channels = config.channels() as usize;
    let stream_config: cpal::StreamConfig = config.config();

    let mut engine = CallbackEngine::new(shared, sample_rate, channels);

    let err_fn = |e| eprintln!("audio stream error: {e}");
    let stream = match config.sample_format() {
        cpal::SampleFormat::F32 => device.build_output_stream(
            &stream_config,
            move |data: &mut [f32], _| engine.fill_f32(data),
            err_fn,
            None,
        )?,
        cpal::SampleFormat::I16 => device.build_output_stream(
            &stream_config,
            move |data: &mut [i16], _| engine.fill_i16(data),
            err_fn,
            None,
        )?,
        other => return Err(anyhow!("unsupported output sample format: {other:?}")),
    };
    Ok((stream, sample_rate))
}

struct CallbackEngine {
    shared: Arc<SharedState>,
    channels: usize,
    chain: DspChain,
    preset: Preset,
    seen_version: u64,
    scratch: Vec<f32>,
    f32_buf: Vec<f32>,
}

impl CallbackEngine {
    fn new(shared: Arc<SharedState>, sample_rate: u32, channels: usize) -> Self {
        Self {
            shared,
            channels,
            chain: DspChain::new(sample_rate),
            preset: Preset::default(),
            seen_version: 0,
            scratch: Vec::new(),
            f32_buf: Vec::new(),
        }
    }

    fn fill_i16(&mut self, data: &mut [i16]) {
        self.f32_buf.resize(data.len(), 0.0);
        let mut buf = std::mem::take(&mut self.f32_buf);
        self.fill_f32(&mut buf);
        for (o, s) in data.iter_mut().zip(buf.iter()) {
            *o = (s.clamp(-1.0, 1.0) * 32767.0) as i16;
        }
        self.f32_buf = buf;
    }

    fn fill_f32(&mut self, data: &mut [f32]) {
        data.fill(0.0);
        let out_frames = data.len() / self.channels;

        let shared = self.shared.clone();
        if !shared.playing.load(Ordering::Relaxed) {
            return;
        }
        let track_guard = match shared.track.try_read() {
            Ok(g) => g,
            Err(_) => return, // a track is being swapped in; play silence
        };
        let track = match track_guard.as_ref() {
            Some(t) => t.clone(),
            None => return,
        };
        drop(track_guard);

        // Pick up parameter changes without blocking the audio thread.
        let version = shared.preset_version.load(Ordering::Acquire);
        if version != self.seen_version {
            if let Ok(p) = shared.preset.try_lock() {
                self.preset = p.clone();
                self.seen_version = version;
            }
        }

        let pos = shared.position.load(Ordering::Relaxed);
        if pos >= track.frames {
            shared.playing.store(false, Ordering::Relaxed);
            return;
        }
        let frames = out_frames.min(track.frames - pos);
        let start = pos * 2;
        let end = (pos + frames) * 2;

        self.scratch.resize(frames * 2, 0.0);
        let bypass = shared.bypass.load(Ordering::Relaxed);
        if bypass {
            self.scratch.copy_from_slice(&track.dry[start..end]);
        } else {
            mix_denoise(
                &track.dry[start..end],
                if track.wet.is_empty() { &[] } else { &track.wet[start..end] },
                self.preset.denoise_amount,
                &mut self.scratch,
            );
            let auto_gain = load_f32(&shared.auto_gain_db);
            let m = self.chain.process_block(&mut self.scratch, &self.preset, auto_gain);
            store_f32(&shared.meters.peak_l, m.peak_l);
            store_f32(&shared.meters.peak_r, m.peak_r);
            store_f32(&shared.meters.gain_reduction_db, m.gain_reduction_db);
        }
        if bypass {
            let mut pl = 0f32;
            let mut pr = 0f32;
            for f in self.scratch.chunks_exact(2) {
                pl = pl.max(f[0].abs());
                pr = pr.max(f[1].abs());
            }
            store_f32(&shared.meters.peak_l, pl);
            store_f32(&shared.meters.peak_r, pr);
            store_f32(&shared.meters.gain_reduction_db, 0.0);
        }

        for i in 0..frames {
            let base = i * self.channels;
            data[base] = self.scratch[i * 2];
            if self.channels > 1 {
                data[base + 1] = self.scratch[i * 2 + 1];
            }
        }
        shared.position.store(pos + frames, Ordering::Relaxed);
    }
}
