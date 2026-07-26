// Prevents an extra console window on Windows in release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod pipeline;

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use clearwave_engine::player::{load_f32, Player, SharedState, TrackBuffers};
use clearwave_engine::preset::Preset;
use clearwave_engine::profile::Profile;
use clearwave_engine::{decode, denoise, loudness, render, resample, waveform};
use serde::Serialize;
use tauri::{Emitter, Manager, State};

/// LUFS of the loaded track's dry/denoised buffers, used to keep the live
/// loudness-normalization preview in sync as parameters move.
struct LoadedInfo {
    path: PathBuf,
    lufs_dry: f64,
    lufs_wet: f64,
    /// Raw 24-band measurement of the loaded track, for reference matching.
    band_db: Vec<f32>,
}

struct AppState {
    shared: Arc<SharedState>,
    /// Engine/playback sample rate. Falls back to 48 kHz when no audio
    /// device exists (e.g. CI); export always works regardless.
    engine_rate: u32,
    playback_ok: bool,
    _player: Mutex<Option<Player>>,
    loaded: Mutex<Option<LoadedInfo>>,
    batch_cancel: Arc<AtomicBool>,
    batch_running: Arc<AtomicBool>,
}

#[derive(Serialize)]
struct TrackInfo {
    file_name: String,
    path: String,
    duration_seconds: f64,
    engine_rate: u32,
    source_rate: u32,
    lufs_dry: f64,
    lufs_wet: f64,
    waveform_min: Vec<f32>,
    waveform_max: Vec<f32>,
    playback_ok: bool,
}

#[derive(Serialize)]
struct Status {
    has_track: bool,
    playing: bool,
    position_seconds: f64,
    duration_seconds: f64,
    peak_l: f32,
    peak_r: f32,
    gain_reduction_db: f32,
    bypass: bool,
    batch_running: bool,
}

#[derive(Serialize)]
struct Tools {
    ffmpeg: bool,
    deepfilter: bool,
    demucs: bool,
    uvr: bool,
}

#[derive(Clone, Serialize)]
struct BatchProgress {
    index: usize,
    total: usize,
    file: String,
    stage: String,
    error: Option<String>,
    done: bool,
}

fn approx_source_lufs(info: &LoadedInfo, denoise_amount: f32) -> f64 {
    // Interpolate loudness between dry and denoised in the power domain —
    // close enough for live preview; export measures exactly.
    let a = denoise_amount.clamp(0.0, 1.0) as f64;
    let p_dry = 10f64.powf(info.lufs_dry / 10.0);
    let p_wet = 10f64.powf(info.lufs_wet / 10.0);
    let mixed = p_dry * (1.0 - a) + p_wet * a;
    10.0 * mixed.log10()
}

fn refresh_auto_gain(state: &AppState, preset: &Preset) {
    let loaded = state.loaded.lock().unwrap();
    if let Some(info) = loaded.as_ref() {
        let source = approx_source_lufs(info, preset.denoise_amount);
        let gain = loudness::normalization_gain_db(source, preset.target_lufs as f64);
        state.shared.set_auto_gain_db(gain as f32);
    }
}

#[tauri::command]
async fn load_track(path: String, state: State<'_, AppState>) -> Result<TrackInfo, String> {
    let engine_rate = state.engine_rate;
    let shared = state.shared.clone();
    let path_buf = PathBuf::from(&path);

    let result = tauri::async_runtime::spawn_blocking(move || -> anyhow::Result<_> {
        let native = decode::decode_file(&path_buf)?;
        let source_rate = native.sample_rate;
        let dry = resample::resample(&native, engine_rate)?;
        drop(native);
        // Precompute the AI-denoised twin once so the denoise slider is a
        // zero-latency crossfade during playback.
        let wet = denoise::denoise(&dry)?;
        let lufs_dry = loudness::integrated_lufs(&dry).unwrap_or(-70.0);
        let lufs_wet = loudness::integrated_lufs(&wet).unwrap_or(lufs_dry);
        let pk = waveform::peaks(&dry, 1200);
        let band_db = clearwave_engine::profile::measure_bands(
            engine_rate,
            &dry.samples,
            &clearwave_engine::dsp::match_centers(),
        );
        Ok((dry, wet, lufs_dry, lufs_wet, pk, source_rate, band_db))
    })
    .await
    .map_err(|e| e.to_string())?
    .map_err(|e| e.to_string())?;

    let (dry, wet, lufs_dry, lufs_wet, pk, source_rate, band_db) = result;
    let duration = dry.duration_seconds();
    let frames = dry.frames();

    shared.playing.store(false, Ordering::Relaxed);
    shared.position.store(0, Ordering::Relaxed);
    *shared.track.write().unwrap() = Some(Arc::new(TrackBuffers {
        dry: dry.samples,
        wet: wet.samples,
        frames,
    }));

    let file_name = PathBuf::from(&path)
        .file_name()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_else(|| path.clone());

    *state.loaded.lock().unwrap() = Some(LoadedInfo {
        path: PathBuf::from(&path),
        lufs_dry,
        lufs_wet,
        band_db,
    });
    let preset = state.shared.preset.lock().unwrap().clone();
    refresh_auto_gain(&state, &preset);

    Ok(TrackInfo {
        file_name,
        path,
        duration_seconds: duration,
        engine_rate,
        source_rate,
        lufs_dry,
        lufs_wet,
        waveform_min: pk.iter().map(|p| p.0).collect(),
        waveform_max: pk.iter().map(|p| p.1).collect(),
        playback_ok: state.playback_ok,
    })
}

#[tauri::command]
fn play(state: State<'_, AppState>) -> Result<(), String> {
    if !state.playback_ok {
        return Err("No audio output device available".into());
    }
    let shared = &state.shared;
    if shared.track.read().unwrap().is_none() {
        return Err("No track loaded".into());
    }
    // Restart from the top if we're at the end.
    if let Some(t) = shared.track.read().unwrap().as_ref() {
        if shared.position.load(Ordering::Relaxed) >= t.frames {
            shared.position.store(0, Ordering::Relaxed);
        }
    }
    shared.playing.store(true, Ordering::Relaxed);
    Ok(())
}

#[tauri::command]
fn pause(state: State<'_, AppState>) {
    state.shared.playing.store(false, Ordering::Relaxed);
}

#[tauri::command]
fn seek(seconds: f64, state: State<'_, AppState>) {
    let frame = (seconds.max(0.0) * state.engine_rate as f64) as usize;
    let max = state
        .shared
        .track
        .read()
        .unwrap()
        .as_ref()
        .map(|t| t.frames)
        .unwrap_or(0);
    state
        .shared
        .position
        .store(frame.min(max.saturating_sub(1)), Ordering::Relaxed);
}

#[tauri::command]
fn set_bypass(bypass: bool, state: State<'_, AppState>) {
    state.shared.bypass.store(bypass, Ordering::Relaxed);
}

#[tauri::command]
fn set_params(preset: Preset, state: State<'_, AppState>) {
    refresh_auto_gain(&state, &preset);
    state.shared.set_preset(preset);
}

#[tauri::command]
fn get_status(state: State<'_, AppState>) -> Status {
    let shared = &state.shared;
    let (has_track, frames) = match shared.track.read().unwrap().as_ref() {
        Some(t) => (true, t.frames),
        None => (false, 0),
    };
    Status {
        has_track,
        playing: shared.playing.load(Ordering::Relaxed),
        position_seconds: shared.position.load(Ordering::Relaxed) as f64 / state.engine_rate as f64,
        duration_seconds: frames as f64 / state.engine_rate as f64,
        peak_l: load_f32(&shared.meters.peak_l),
        peak_r: load_f32(&shared.meters.peak_r),
        gain_reduction_db: load_f32(&shared.meters.gain_reduction_db),
        bypass: shared.bypass.load(Ordering::Relaxed),
        batch_running: state.batch_running.load(Ordering::Relaxed),
    }
}

#[tauri::command]
async fn auto_settings(
    base: Preset,
    state: State<'_, AppState>,
) -> Result<clearwave_engine::analyze::AutoReport, String> {
    let track = state
        .shared
        .track
        .read()
        .unwrap()
        .clone()
        .ok_or("Load a track first")?;
    let rate = state.engine_rate;
    let report = tauri::async_runtime::spawn_blocking(move || {
        let wet = if track.wet.is_empty() { None } else { Some(track.wet.as_slice()) };
        clearwave_engine::analyze::auto_preset(rate, &track.dry, wet, &base)
    })
    .await
    .map_err(|e| e.to_string())?;
    // Push the suggested settings straight into the live engine.
    refresh_auto_gain(&state, &report.preset);
    state.shared.set_preset(report.preset.clone());
    Ok(report)
}

#[tauri::command]
fn save_preset(path: String, preset: Preset) -> Result<(), String> {
    let json = serde_json::to_string_pretty(&preset).map_err(|e| e.to_string())?;
    std::fs::write(path, json).map_err(|e| e.to_string())
}

#[tauri::command]
fn load_preset(path: String) -> Result<Preset, String> {
    let json = std::fs::read_to_string(path).map_err(|e| e.to_string())?;
    serde_json::from_str(&json).map_err(|e| e.to_string())
}

#[tauri::command]
fn detect_tools() -> Tools {
    Tools {
        ffmpeg: render::tool_available("ffmpeg", "-version"),
        deepfilter: render::tool_available("deep-filter", "--version"),
        demucs: render::tool_available("demucs", "--help"),
        uvr: render::tool_available("audio-separator", "--version"),
    }
}

#[tauri::command]
async fn build_profile(files: Vec<String>, name: String) -> Result<Profile, String> {
    tauri::async_runtime::spawn_blocking(move || -> anyhow::Result<Profile> {
        let centers = clearwave_engine::dsp::match_centers();
        let mut per_track = Vec::new();
        for f in &files {
            let native = clearwave_engine::decode::decode_file(f.as_ref())?;
            let at48 = resample::resample(&native, 48_000)?;
            per_track.push(clearwave_engine::profile::measure_bands(
                48_000,
                &at48.samples,
                &centers,
            ));
        }
        clearwave_engine::profile::profile_from_tracks(&name, &per_track)
    })
    .await
    .map_err(|e| e.to_string())?
    .map_err(|e| format!("{e:#}"))
}

#[tauri::command]
fn match_gains_current(
    reference: Profile,
    strength: f32,
    state: State<'_, AppState>,
) -> Result<Vec<f32>, String> {
    let loaded = state.loaded.lock().unwrap();
    let info = loaded.as_ref().ok_or("Load a track first")?;
    clearwave_engine::profile::match_gains(&reference, &info.band_db, strength)
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn save_profile(path: String, reference: Profile) -> Result<(), String> {
    let json = serde_json::to_string_pretty(&reference).map_err(|e| e.to_string())?;
    std::fs::write(path, json).map_err(|e| e.to_string())
}

#[tauri::command]
fn load_profile(path: String) -> Result<Profile, String> {
    let json = std::fs::read_to_string(path).map_err(|e| e.to_string())?;
    serde_json::from_str(&json).map_err(|e| e.to_string())
}

#[tauri::command]
async fn export_track(
    output_path: String,
    format: String,
    preset: Preset,
    external_cmd: Option<String>,
    external_pick: Option<String>,
    reference: Option<Profile>,
    state: State<'_, AppState>,
) -> Result<String, String> {
    let input = state
        .loaded
        .lock()
        .unwrap()
        .as_ref()
        .map(|i| i.path.clone())
        .ok_or("No track loaded")?;
    let out_path = output_path.clone();
    tauri::async_runtime::spawn_blocking(move || {
        pipeline::process_and_export(
            &input,
            &PathBuf::from(&out_path),
            &format,
            &preset,
            external_cmd.as_deref(),
            external_pick.as_deref(),
            reference.as_ref(),
        )
    })
    .await
    .map_err(|e| e.to_string())?
    .map_err(|e| format!("{e:#}"))?;
    Ok(format!("Exported to {output_path}"))
}

#[tauri::command]
fn run_batch(
    app: tauri::AppHandle,
    files: Vec<String>,
    output_dir: String,
    format: String,
    preset: Preset,
    external_cmd: Option<String>,
    external_pick: Option<String>,
    reference: Option<Profile>,
    state: State<'_, AppState>,
) -> Result<(), String> {
    if state.batch_running.swap(true, Ordering::SeqCst) {
        return Err("A batch is already running".into());
    }
    state.batch_cancel.store(false, Ordering::SeqCst);
    let cancel = state.batch_cancel.clone();
    let running = state.batch_running.clone();

    std::thread::spawn(move || {
        let total = files.len();
        for (index, file) in files.iter().enumerate() {
            if cancel.load(Ordering::SeqCst) {
                break;
            }
            let name = PathBuf::from(file)
                .file_name()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_else(|| file.clone());
            let _ = app.emit(
                "batch-progress",
                BatchProgress {
                    index,
                    total,
                    file: name.clone(),
                    stage: "processing".into(),
                    error: None,
                    done: false,
                },
            );
            let out = pipeline::batch_output_path(&PathBuf::from(file), &PathBuf::from(&output_dir), &format);
            let result = pipeline::process_and_export(
                &PathBuf::from(file),
                &out,
                &format,
                &preset,
                external_cmd.as_deref(),
                external_pick.as_deref(),
                reference.as_ref(),
            );
            let _ = app.emit(
                "batch-progress",
                BatchProgress {
                    index,
                    total,
                    file: name,
                    stage: if result.is_ok() { "done".into() } else { "error".into() },
                    error: result.err().map(|e| format!("{e:#}")),
                    done: false,
                },
            );
        }
        running.store(false, Ordering::SeqCst);
        let _ = app.emit(
            "batch-progress",
            BatchProgress {
                index: total,
                total,
                file: String::new(),
                stage: "finished".into(),
                error: None,
                done: true,
            },
        );
    });
    Ok(())
}

#[tauri::command]
fn cancel_batch(state: State<'_, AppState>) {
    state.batch_cancel.store(true, Ordering::SeqCst);
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let shared = Arc::new(SharedState::default());
            let (player, engine_rate, playback_ok) = match Player::start(shared.clone()) {
                Ok(p) => {
                    let rate = p.sample_rate;
                    (Some(p), rate, true)
                }
                Err(e) => {
                    eprintln!("audio output unavailable: {e:#}");
                    (None, 48_000, false)
                }
            };
            app.manage(AppState {
                shared,
                engine_rate,
                playback_ok,
                _player: Mutex::new(player),
                loaded: Mutex::new(None),
                batch_cancel: Arc::new(AtomicBool::new(false)),
                batch_running: Arc::new(AtomicBool::new(false)),
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            load_track,
            play,
            pause,
            seek,
            set_bypass,
            set_params,
            get_status,
            auto_settings,
            save_preset,
            load_preset,
            detect_tools,
            build_profile,
            match_gains_current,
            save_profile,
            load_profile,
            export_track,
            run_batch,
            cancel_batch,
        ])
        .run(tauri::generate_context!())
        .expect("error while running ClearWave");
}
