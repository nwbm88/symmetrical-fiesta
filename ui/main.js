/* ClearWave UI — talks to the Rust engine via Tauri commands. */
"use strict";

const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;
const dialog = window.__TAURI__.dialog;

const $ = (id) => document.getElementById(id);

/* ------------------------------------------------ state ---- */

const tracks = []; // { path, name, state: ''|'ok'|'err' }
let currentIndex = -1;
let currentInfo = null; // TrackInfo from load_track
let waveMin = [], waveMax = [];
let statusTimer = null;
let lastStatus = null;
let batchTotal = 0;

const AUDIO_EXTS = ["mp3", "m4a", "aac", "flac", "wav", "ogg", "opus", "webm", "mkv", "mp4", "aiff", "alac", "caf"];

/* ------------------------------------------------ preset ---- */

function collectPreset() {
  return {
    version: 1,
    denoise_amount: Number($("p-denoise").value) / 100,
    hpf_enabled: $("p-hpf-on").checked,
    hpf_freq: Number($("p-hpf").value),
    eq_enabled: $("p-eq-on").checked,
    low_shelf_freq: Number($("p-eq-ls-f").value),
    low_shelf_gain_db: Number($("p-eq-ls").value),
    peak1_freq: Number($("p-eq-p1-f").value),
    peak1_gain_db: Number($("p-eq-p1").value),
    peak1_q: 1.0,
    peak2_freq: Number($("p-eq-p2-f").value),
    peak2_gain_db: Number($("p-eq-p2").value),
    peak2_q: 1.0,
    peak3_freq: Number($("p-eq-p3-f").value),
    peak3_gain_db: Number($("p-eq-p3").value),
    peak3_q: 1.0,
    high_shelf_freq: Number($("p-eq-hs-f").value),
    high_shelf_gain_db: Number($("p-eq-hs").value),
    comp_enabled: $("p-comp-on").checked,
    comp_threshold_db: Number($("p-th").value),
    comp_ratio: Number($("p-ratio").value),
    comp_attack_ms: Number($("p-att").value),
    comp_release_ms: Number($("p-rel").value),
    comp_makeup_db: Number($("p-mk").value),
    loudness_enabled: $("p-loud-on").checked,
    target_lufs: Number($("p-lufs").value),
    output_gain_db: Number($("p-trim").value),
    limiter_ceiling_db: Number($("p-ceil").value),
  };
}

function applyPresetToUI(p) {
  $("p-denoise").value = Math.round((p.denoise_amount ?? 0) * 100);
  $("p-hpf-on").checked = !!p.hpf_enabled;
  $("p-hpf").value = p.hpf_freq ?? 30;
  $("p-eq-on").checked = !!p.eq_enabled;
  $("p-eq-ls").value = p.low_shelf_gain_db ?? 0;
  $("p-eq-ls-f").value = p.low_shelf_freq ?? 120;
  $("p-eq-p1").value = p.peak1_gain_db ?? 0;
  $("p-eq-p1-f").value = p.peak1_freq ?? 250;
  $("p-eq-p2").value = p.peak2_gain_db ?? 0;
  $("p-eq-p2-f").value = p.peak2_freq ?? 1000;
  $("p-eq-p3").value = p.peak3_gain_db ?? 0;
  $("p-eq-p3-f").value = p.peak3_freq ?? 4000;
  $("p-eq-hs").value = p.high_shelf_gain_db ?? 0;
  $("p-eq-hs-f").value = p.high_shelf_freq ?? 8000;
  $("p-comp-on").checked = !!p.comp_enabled;
  $("p-th").value = p.comp_threshold_db ?? -18;
  $("p-ratio").value = p.comp_ratio ?? 2.5;
  $("p-att").value = p.comp_attack_ms ?? 15;
  $("p-rel").value = p.comp_release_ms ?? 150;
  $("p-mk").value = p.comp_makeup_db ?? 0;
  $("p-loud-on").checked = p.loudness_enabled ?? true;
  $("p-lufs").value = p.target_lufs ?? -14;
  $("p-trim").value = p.output_gain_db ?? 0;
  $("p-ceil").value = p.limiter_ceiling_db ?? -1;
  refreshOutputs();
  pushParams();
}

/* Debounced parameter push so slider drags stay smooth. */
let pushTimer = null;
function pushParams() {
  refreshOutputs();
  clearTimeout(pushTimer);
  pushTimer = setTimeout(async () => {
    try {
      await invoke("set_params", { preset: collectPreset() });
    } catch (e) {
      setStatus(String(e), "error");
    }
  }, 40);
}

function refreshOutputs() {
  $("o-denoise").textContent = `${$("p-denoise").value}%`;
  $("o-hpf").textContent = `${$("p-hpf").value} Hz`;
  for (const [slider, out] of [
    ["p-eq-ls", "o-eq-ls"], ["p-eq-p1", "o-eq-p1"], ["p-eq-p2", "o-eq-p2"],
    ["p-eq-p3", "o-eq-p3"], ["p-eq-hs", "o-eq-hs"],
  ]) {
    const v = Number($(slider).value);
    $(out).textContent = (v > 0 ? "+" : "") + v.toFixed(1).replace(/\.0$/, "");
  }
  $("o-th").textContent = `${$("p-th").value} dB`;
  $("o-ratio").textContent = `${Number($("p-ratio").value).toFixed(1)}:1`;
  $("o-att").textContent = `${$("p-att").value} ms`;
  $("o-rel").textContent = `${$("p-rel").value} ms`;
  $("o-mk").textContent = `${$("p-mk").value} dB`;
  $("o-lufs").textContent = `${Number($("p-lufs").value).toFixed(1)} LUFS`;
  $("o-trim").textContent = `${$("p-trim").value} dB`;
  $("o-ceil").textContent = `${Number($("p-ceil").value).toFixed(1)} dB`;
  updateLoudInfo();
}

function updateLoudInfo() {
  if (!currentInfo) return;
  const target = Number($("p-lufs").value);
  const src = currentInfo.lufs_dry;
  const gain = target - src;
  $("loud-info").textContent =
    `Source: ${src.toFixed(1)} LUFS → target ${target.toFixed(1)} LUFS ` +
    `(${gain >= 0 ? "+" : ""}${gain.toFixed(1)} dB). Applied identically across the album at export.`;
}

/* ------------------------------------------------ status ---- */

function setStatus(msg, cls = "") {
  const el = $("status-msg");
  el.textContent = msg;
  el.className = cls;
}

function fmtTime(s) {
  if (!isFinite(s) || s < 0) s = 0;
  const m = Math.floor(s / 60);
  const sec = (s - m * 60).toFixed(1).padStart(4, "0");
  return `${m}:${sec}`;
}

async function pollStatus() {
  try {
    const st = await invoke("get_status");
    lastStatus = st;
    $("time-now").textContent = fmtTime(st.position_seconds);
    $("time-total").textContent = fmtTime(st.duration_seconds);
    $("btn-play").textContent = st.playing ? "⏸" : "▶";
    drawWave(st.duration_seconds > 0 ? st.position_seconds / st.duration_seconds : 0);

    const toPct = (v) => {
      const db = 20 * Math.log10(Math.max(v, 1e-4));
      return Math.max(0, Math.min(100, (db + 60) / 60 * 100));
    };
    $("m-l").style.width = `${toPct(st.peak_l)}%`;
    $("m-r").style.width = `${toPct(st.peak_r)}%`;
    $("m-gr").style.width = `${Math.min(100, (st.gain_reduction_db / 20) * 100)}%`;
  } catch {
    /* backend busy; skip one frame */
  }
}

/* ------------------------------------------------ waveform ---- */

function drawWave(progress = 0) {
  const canvas = $("waveform");
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== w * dpr) { canvas.width = w * dpr; canvas.height = h * dpr; }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  if (!waveMin.length) {
    ctx.fillStyle = "#4a5b74";
    ctx.font = "12px Segoe UI, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("Load a track to see its waveform", w / 2, h / 2);
    return;
  }

  const mid = h / 2;
  const n = waveMin.length;
  const bw = w / n;
  const playX = progress * w;
  for (let i = 0; i < n; i++) {
    const x = i * bw;
    const y1 = mid - waveMax[i] * (mid - 4);
    const y2 = mid - waveMin[i] * (mid - 4);
    ctx.fillStyle = x <= playX ? "#4dd7ff" : "#27476b";
    ctx.fillRect(x, y1, Math.max(bw - 0.5, 0.8), Math.max(y2 - y1, 1));
  }
  // playhead
  ctx.fillStyle = "#eaf7ff";
  ctx.fillRect(playX - 0.5, 0, 1.5, h);
}

$("waveform").addEventListener("click", async (e) => {
  if (!lastStatus || !lastStatus.has_track) return;
  const rect = e.currentTarget.getBoundingClientRect();
  const frac = (e.clientX - rect.left) / rect.width;
  await invoke("seek", { seconds: frac * lastStatus.duration_seconds });
});

/* ------------------------------------------------ tracks ---- */

function renderTrackList() {
  const ul = $("track-list");
  ul.innerHTML = "";
  if (!tracks.length) {
    const li = document.createElement("li");
    li.className = "empty-hint";
    li.innerHTML = "Add the tracks of your album here.<br/>Click one to open it in the editor.";
    ul.appendChild(li);
    return;
  }
  tracks.forEach((t, i) => {
    const li = document.createElement("li");
    li.className = i === currentIndex ? "active" : "";
    const name = document.createElement("span");
    name.className = "tname";
    name.textContent = t.name;
    name.title = t.path;
    const state = document.createElement("span");
    state.className = `tstate ${t.state || ""}`;
    state.textContent = t.state === "ok" ? "✓" : t.state === "err" ? "!" : "";
    li.append(name, state);
    li.addEventListener("click", () => loadTrack(i));
    ul.appendChild(li);
  });
}

async function addTracks() {
  const sel = await dialog.open({
    multiple: true,
    title: "Add audio tracks",
    filters: [{ name: "Audio", extensions: AUDIO_EXTS }],
  });
  if (!sel) return;
  const paths = Array.isArray(sel) ? sel : [sel];
  for (const p of paths) {
    if (!tracks.some((t) => t.path === p)) {
      tracks.push({ path: p, name: p.split(/[\\/]/).pop(), state: "" });
    }
  }
  renderTrackList();
  if (currentIndex < 0 && tracks.length) loadTrack(0);
}

async function loadTrack(index) {
  const t = tracks[index];
  if (!t) return;
  currentIndex = index;
  renderTrackList();
  $("load-overlay").classList.remove("hidden");
  $("load-msg").textContent = `Decoding + AI noise analysis: ${t.name}`;
  setStatus(`Loading ${t.name}…`);
  try {
    const info = await invoke("load_track", { path: t.path });
    currentInfo = info;
    waveMin = info.waveform_min;
    waveMax = info.waveform_max;
    t.state = "ok";
    $("track-title").textContent = info.file_name;
    $("track-sub").textContent =
      `${fmtTime(info.duration_seconds)} · source ${info.source_rate} Hz · ` +
      `measured ${info.lufs_dry.toFixed(1)} LUFS` +
      (info.playback_ok ? "" : " · ⚠ no audio device — preview disabled, export still works");
    $("btn-play").disabled = !info.playback_ok;
    updateLoudInfo();
    await invoke("set_params", { preset: collectPreset() });
    setStatus(`Loaded ${info.file_name}. Tweak the modules while it plays — then remaster the whole album.`, "ok");
  } catch (e) {
    t.state = "err";
    setStatus(`Could not load ${t.name}: ${e}`, "error");
  } finally {
    $("load-overlay").classList.add("hidden");
    renderTrackList();
    drawWave(0);
  }
}

/* ------------------------------------------------ transport ---- */

$("btn-play").addEventListener("click", async () => {
  try {
    if (lastStatus && lastStatus.playing) await invoke("pause");
    else await invoke("play");
  } catch (e) {
    setStatus(String(e), "error");
  }
});

document.addEventListener("keydown", (e) => {
  if (e.code === "Space" && !["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName)) {
    e.preventDefault();
    $("btn-play").click();
  }
});

/* A/B: click toggles; hold-to-compare also works via mousedown/up */
const abBtn = $("btn-bypass");
let abState = false;
async function setAB(on) {
  abState = on;
  abBtn.classList.toggle("active", on);
  await invoke("set_bypass", { bypass: on });
}
abBtn.addEventListener("click", () => setAB(!abState));

/* ------------------------------------------------ presets ---- */

$("btn-save-preset").addEventListener("click", async () => {
  const path = await dialog.save({
    title: "Save preset",
    defaultPath: "clearwave-preset.json",
    filters: [{ name: "ClearWave preset", extensions: ["json"] }],
  });
  if (!path) return;
  try {
    await invoke("save_preset", { path, preset: collectPreset() });
    setStatus(`Preset saved: ${path}`, "ok");
  } catch (e) {
    setStatus(`Preset save failed: ${e}`, "error");
  }
});

$("btn-load-preset").addEventListener("click", async () => {
  const path = await dialog.open({
    title: "Load preset",
    multiple: false,
    filters: [{ name: "ClearWave preset", extensions: ["json"] }],
  });
  if (!path) return;
  try {
    const p = await invoke("load_preset", { path });
    applyPresetToUI(p);
    setStatus(`Preset loaded: ${path}`, "ok");
  } catch (e) {
    setStatus(`Preset load failed: ${e}`, "error");
  }
});

/* ------------------------------------------------ export ---- */

let outDir = "";

$("btn-out-dir").addEventListener("click", async () => {
  const dir = await dialog.open({ directory: true, title: "Choose output folder" });
  if (dir) {
    outDir = dir;
    $("out-dir").value = dir;
  }
});

$("ai-external").addEventListener("change", () => {
  $("ai-custom-cmd").classList.toggle("hidden", $("ai-external").value !== "custom");
});

function externalCmd() {
  const mode = $("ai-external").value;
  if (mode === "deepfilter") return "deep-filter {in} -o {outdir}";
  if (mode === "custom") return $("ai-custom-cmd").value.trim() || null;
  return null;
}

$("btn-export-one").addEventListener("click", async () => {
  if (currentIndex < 0) return setStatus("Load a track first.", "error");
  const fmt = $("out-format").value;
  const stem = tracks[currentIndex].name.replace(/\.[^.]+$/, "");
  const path = await dialog.save({
    title: "Export remastered track",
    defaultPath: `${stem} [remastered].${fmt}`,
    filters: [{ name: fmt.toUpperCase(), extensions: [fmt] }],
  });
  if (!path) return;
  setStatus("Rendering… (two-pass loudness, this takes a moment)");
  $("btn-export-one").disabled = true;
  try {
    const msg = await invoke("export_track", {
      outputPath: path,
      format: fmt,
      preset: collectPreset(),
      externalCmd: externalCmd(),
    });
    setStatus(msg, "ok");
  } catch (e) {
    setStatus(`Export failed: ${e}`, "error");
  } finally {
    $("btn-export-one").disabled = false;
  }
});

$("btn-export-all").addEventListener("click", async () => {
  if (!tracks.length) return setStatus("Add tracks first.", "error");
  if (!outDir) return setStatus("Choose an output folder first.", "error");
  batchTotal = tracks.length;
  $("batch-progress").classList.remove("hidden");
  $("btn-cancel-batch").classList.remove("hidden");
  $("btn-export-all").disabled = true;
  $("batch-bar").style.width = "0%";
  $("batch-label").textContent = "Starting…";
  try {
    await invoke("run_batch", {
      files: tracks.map((t) => t.path),
      outputDir: outDir,
      format: $("out-format").value,
      preset: collectPreset(),
      externalCmd: externalCmd(),
    });
  } catch (e) {
    setStatus(`Batch failed to start: ${e}`, "error");
    $("btn-export-all").disabled = false;
    $("btn-cancel-batch").classList.add("hidden");
  }
});

$("btn-cancel-batch").addEventListener("click", () => invoke("cancel_batch"));

listen("batch-progress", (ev) => {
  const p = ev.payload;
  if (p.done) {
    $("batch-bar").style.width = "100%";
    $("batch-label").textContent = "Batch finished.";
    $("btn-export-all").disabled = false;
    $("btn-cancel-batch").classList.add("hidden");
    setStatus("Album remaster complete ✓ — all tracks normalized to the same loudness.", "ok");
    return;
  }
  const frac = batchTotal ? ((p.index + (p.stage === "done" ? 1 : 0.5)) / batchTotal) : 0;
  $("batch-bar").style.width = `${Math.round(frac * 100)}%`;
  if (p.stage === "processing") {
    $("batch-label").textContent = `(${p.index + 1}/${p.total}) ${p.file}…`;
  } else if (p.stage === "error") {
    setStatus(`${p.file}: ${p.error}`, "error");
  }
});

/* ------------------------------------------------ wiring ---- */

$("btn-add-tracks").addEventListener("click", addTracks);

for (const el of document.querySelectorAll("input[type=range], input[type=number], input[type=checkbox]")) {
  el.addEventListener("input", pushParams);
}

async function detectTools() {
  try {
    const t = await invoke("detect_tools");
    $("tool-ffmpeg").classList.toggle("on", t.ffmpeg);
    $("tool-deepfilter").classList.toggle("on", t.deepfilter);
    $("tool-demucs").classList.toggle("on", t.demucs);
    $("tool-ffmpeg").title = t.ffmpeg
      ? "ffmpeg found — MP3/FLAC/M4A export enabled"
      : "ffmpeg not found — install it to export MP3/FLAC/M4A (WAV always works)";
    $("tool-deepfilter").title = t.deepfilter
      ? "DeepFilterNet found — available under Deep clean"
      : "DeepFilterNet not found — see README to install (optional)";
    $("tool-demucs").title = t.demucs
      ? "Demucs found — usable via custom Deep clean command"
      : "Demucs not found — see README to install (optional)";
  } catch { /* ignore */ }
}

window.addEventListener("resize", () => drawWave(
  lastStatus && lastStatus.duration_seconds > 0
    ? lastStatus.position_seconds / lastStatus.duration_seconds
    : 0
));

refreshOutputs();
drawWave(0);
detectTools();
invoke("set_params", { preset: collectPreset() }).catch(() => {});
statusTimer = setInterval(pollStatus, 100);
