/* ClearWave UI — talks to the Rust engine via Tauri commands. */
"use strict";

const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;
const dialog = window.__TAURI__.dialog;

const $ = (id) => document.getElementById(id);

/* ══════════════════════ state ══════════════════════ */

const tracks = []; // { path, name, state: ''|'ok'|'err' }
let currentIndex = -1;
let currentInfo = null; // TrackInfo from load_track
let refProfile = null; // Profile learned from reference songs
let matchGains = []; // per-track gains computed against refProfile
let waveMin = [], waveMax = [];
let lastStatus = null;
let batchTotal = 0;
let outDir = "";
let advanced = false;

const AUDIO_EXTS = ["mp3", "m4a", "aac", "flac", "wav", "ogg", "opus", "webm", "mkv", "mp4", "aiff", "alac", "caf"];

/* ══════════════════════ preset ══════════════════════ */

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
    match_enabled: $("p-match-on").checked && !!refProfile,
    match_strength: Number($("p-match-strength").value) / 100,
    match_gains: $("p-match-on").checked ? matchGains : [],
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
  syncSimpleFromAdvanced();
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

/* ── Simple ⇄ Advanced mirroring ─────────────────────
   Simple mode drives the same engine parameters through friendlier
   controls: one "tone" tilt maps to the low/high shelves. */

function syncAdvancedFromSimple() {
  $("p-denoise").value = $("s-denoise").value;
  const tone = Number($("s-tone").value);
  $("p-eq-on").checked = Math.abs(tone) > 0.01 || eqHasManualBands();
  $("p-eq-hs").value = tone;
  $("p-eq-ls").value = -tone * 0.6;
  $("p-comp-on").checked = $("s-comp").checked;
  $("p-loud-on").checked = $("s-loud").checked;
  $("p-lufs").value = $("s-lufs").value;
}

function syncSimpleFromAdvanced() {
  $("s-denoise").value = $("p-denoise").value;
  $("s-tone").value = $("p-eq-hs").value;
  $("s-comp").checked = $("p-comp-on").checked;
  $("s-loud").checked = $("p-loud-on").checked;
  $("s-lufs").value = $("p-lufs").value;
}

/* True when a band other than the two shelves the tone knob drives is set. */
function eqHasManualBands() {
  return ["p-eq-p1", "p-eq-p2", "p-eq-p3"].some((id) => Math.abs(Number($(id).value)) > 0.01);
}

function refreshOutputs() {
  $("o-denoise").textContent = `${$("p-denoise").value}%`;
  $("o-denoise-simple").textContent = `${$("s-denoise").value}%`;
  $("o-hpf").textContent = `${$("p-hpf").value} Hz`;
  for (const [slider, out] of [
    ["p-eq-ls", "o-eq-ls"], ["p-eq-p1", "o-eq-p1"], ["p-eq-p2", "o-eq-p2"],
    ["p-eq-p3", "o-eq-p3"], ["p-eq-hs", "o-eq-hs"],
  ]) {
    const v = Number($(slider).value);
    $(out).textContent = (v > 0 ? "+" : "") + v.toFixed(1).replace(/\.0$/, "");
  }
  const tone = Number($("s-tone").value);
  $("o-tone").textContent =
    Math.abs(tone) < 0.25 ? "flat" : `${tone > 0 ? "brighter" : "warmer"} ${Math.abs(tone).toFixed(1)} dB`;
  $("o-th").textContent = `${$("p-th").value} dB`;
  $("o-ratio").textContent = `${Number($("p-ratio").value).toFixed(1)}:1`;
  $("o-att").textContent = `${$("p-att").value} ms`;
  $("o-rel").textContent = `${$("p-rel").value} ms`;
  $("o-mk").textContent = `${$("p-mk").value} dB`;
  $("o-lufs").textContent = `${Number($("p-lufs").value).toFixed(1)} LUFS`;
  $("o-lufs-simple").textContent = `${Number($("s-lufs").value).toFixed(1)} LUFS`;
  $("o-trim").textContent = `${$("p-trim").value} dB`;
  $("o-ceil").textContent = `${Number($("p-ceil").value).toFixed(1)} dB`;
  $("o-match").textContent = `${$("p-match-strength").value}%`;
  updateLoudInfo();
}

function updateLoudInfo() {
  if (!currentInfo) return;
  const target = Number($("p-lufs").value);
  const gain = target - currentInfo.lufs_dry;
  $("loud-info").textContent =
    `Source: ${currentInfo.lufs_dry.toFixed(1)} LUFS → target ${target.toFixed(1)} LUFS ` +
    `(${gain >= 0 ? "+" : ""}${gain.toFixed(1)} dB). Applied identically across the album at export.`;
}

/* ══════════════════════ status / helpers ══════════════════════ */

function setStatus(msg, cls = "") {
  const el = $("status-msg");
  el.textContent = msg;
  el.className = cls;
}

function fmtTime(s) {
  if (!isFinite(s) || s < 0) s = 0;
  const m = Math.floor(s / 60);
  return `${m}:${(s - m * 60).toFixed(1).padStart(4, "0")}`;
}

function setMeter(ids, pct) {
  for (const id of ids) {
    const el = $(id);
    if (el) el.style.width = `${pct}%`;
  }
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
      return Math.max(0, Math.min(100, ((db + 60) / 60) * 100));
    };
    setMeter(["m-l", "m-l-adv"], toPct(st.peak_l));
    setMeter(["m-r", "m-r-adv"], toPct(st.peak_r));
    setMeter(["m-gr", "m-gr-adv"], Math.min(100, (st.gain_reduction_db / 20) * 100));
  } catch {
    /* backend busy; skip a frame */
  }
}

/* ══════════════════════ waveform ══════════════════════ */

function drawWave(progress = 0) {
  const canvas = $("waveform");
  if (!canvas.clientWidth) return;
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== Math.round(w * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  if (!waveMin.length) {
    ctx.fillStyle = "#4a5b74";
    ctx.font = "12.5px Segoe UI, sans-serif";
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
    const y1 = mid - waveMax[i] * (mid - 5);
    const y2 = mid - waveMin[i] * (mid - 5);
    ctx.fillStyle = x <= playX ? "#4dd7ff" : "#294c72";
    ctx.fillRect(x, y1, Math.max(bw - 0.5, 0.8), Math.max(y2 - y1, 1.2));
  }
  ctx.fillStyle = "#eaf7ff";
  ctx.fillRect(playX - 0.75, 0, 1.6, h);
}

$("waveform").addEventListener("click", async (e) => {
  if (!lastStatus || !lastStatus.has_track) return;
  const rect = e.currentTarget.getBoundingClientRect();
  const frac = (e.clientX - rect.left) / rect.width;
  await invoke("seek", { seconds: frac * lastStatus.duration_seconds });
});

/* ══════════════════════ modal ══════════════════════ */

let modalResolve = null;

function askForName(title, sub, initial = "") {
  $("modal-title").textContent = title;
  $("modal-sub").textContent = sub;
  $("modal-input").value = initial;
  $("modal-overlay").classList.remove("hidden");
  setTimeout(() => $("modal-input").focus(), 30);
  return new Promise((resolve) => {
    modalResolve = resolve;
  });
}

function closeModal(value) {
  $("modal-overlay").classList.add("hidden");
  if (modalResolve) {
    modalResolve(value);
    modalResolve = null;
  }
}

$("modal-ok").addEventListener("click", () => closeModal($("modal-input").value.trim() || null));
$("modal-cancel").addEventListener("click", () => closeModal(null));
$("modal-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") closeModal($("modal-input").value.trim() || null);
  if (e.key === "Escape") closeModal(null);
});

$("btn-help").addEventListener("click", () => $("help-overlay").classList.remove("hidden"));
$("help-close").addEventListener("click", () => $("help-overlay").classList.add("hidden"));
$("help-overlay").addEventListener("click", (e) => {
  if (e.target === $("help-overlay")) $("help-overlay").classList.add("hidden");
});

/* ══════════════════════ tracks ══════════════════════ */

function renderTrackList() {
  const ul = $("track-list");
  ul.innerHTML = "";
  if (!tracks.length) {
    const li = document.createElement("li");
    li.className = "empty-hint";
    li.innerHTML = "Nothing added yet.<br/>Click <b>Add music files</b> to start.";
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
    title: "Add music files",
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
  $("welcome").classList.add("hidden");
  $("workspace").classList.remove("hidden");
  $("load-overlay").classList.remove("hidden");
  $("load-msg").textContent = `Opening ${t.name} — decoding and analysing noise…`;
  setStatus(`Loading ${t.name}…`);
  try {
    const info = await invoke("load_track", { path: t.path });
    currentInfo = info;
    waveMin = info.waveform_min;
    waveMax = info.waveform_max;
    t.state = "ok";
    $("track-title").textContent = info.file_name;
    $("track-sub").textContent =
      `${fmtTime(info.duration_seconds)} · ${info.source_rate} Hz · ` +
      `measured ${info.lufs_dry.toFixed(1)} LUFS` +
      (info.playback_ok ? "" : " · ⚠ no audio device — preview off, export still works");
    $("btn-play").disabled = !info.playback_ok;
    updateLoudInfo();
    await invoke("set_params", { preset: collectPreset() });
    await refreshMatchGains();
    setStatus(`Loaded ${info.file_name} — press ✨ Fix it for me to get started.`, "ok");
  } catch (e) {
    t.state = "err";
    setStatus(`Could not open ${t.name}: ${e}`, "error");
  } finally {
    $("load-overlay").classList.add("hidden");
    renderTrackList();
    drawWave(0);
  }
}

/* ══════════════════════ transport ══════════════════════ */

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

let abState = false;
async function setAB(on) {
  abState = on;
  $("btn-bypass").classList.toggle("active", on);
  $("btn-bypass").textContent = on ? "Original" : "A / B";
  await invoke("set_bypass", { bypass: on });
}
$("btn-bypass").addEventListener("click", () => setAB(!abState));

/* ══════════════════════ auto mode ══════════════════════ */

$("btn-auto").addEventListener("click", async () => {
  if (currentIndex < 0) return setStatus("Add and open a track first.", "error");
  setStatus("Listening to the track — noise, tone and dynamics…");
  $("btn-auto").disabled = true;
  try {
    const r = await invoke("auto_settings", { base: collectPreset() });
    applyPresetToUI(r.preset);
    $("auto-notes").innerHTML =
      "<b>✨ Here's what I found and changed</b><br/>" + r.notes.map((n) => "• " + n).join("<br/>");
    $("auto-notes").classList.remove("hidden");
    setStatus("Done — have a listen, then tweak anything you like. Use A / B to compare.", "ok");
  } catch (e) {
    setStatus(`Auto settings failed: ${e}`, "error");
  } finally {
    $("btn-auto").disabled = false;
  }
});

/* ══════════════════════ settings presets ══════════════════════ */

async function doSavePreset() {
  const path = await dialog.save({
    title: "Save these settings",
    defaultPath: "clearwave-settings.json",
    filters: [{ name: "ClearWave settings", extensions: ["json"] }],
  });
  if (!path) return;
  try {
    await invoke("save_preset", { path, preset: collectPreset() });
    setStatus(`Settings saved: ${path}`, "ok");
  } catch (e) {
    setStatus(`Could not save settings: ${e}`, "error");
  }
}

async function doLoadPreset() {
  const path = await dialog.open({
    title: "Load settings",
    multiple: false,
    filters: [{ name: "ClearWave settings", extensions: ["json"] }],
  });
  if (!path) return;
  try {
    applyPresetToUI(await invoke("load_preset", { path }));
    setStatus("Settings loaded.", "ok");
  } catch (e) {
    setStatus(`Could not load settings: ${e}`, "error");
  }
}

$("btn-save-preset").addEventListener("click", doSavePreset);
$("btn-load-preset").addEventListener("click", doLoadPreset);
$("btn-save-preset-adv").addEventListener("click", doSavePreset);
$("btn-load-preset-adv").addEventListener("click", doLoadPreset);

/* ══════════════════════ reference profile ══════════════════════ */

function showProfile() {
  const has = !!refProfile;
  $("profile-empty").classList.toggle("hidden", has);
  $("profile-loaded").classList.toggle("hidden", !has);
  if (has) {
    $("profile-name").textContent = refProfile.name || "Untitled profile";
    $("profile-meta").textContent =
      `Learned from ${refProfile.num_tracks} song${refProfile.num_tracks === 1 ? "" : "s"}`;
  }
}

async function refreshMatchGains() {
  if (!refProfile || currentIndex < 0 || !$("p-match-on").checked) {
    matchGains = [];
    pushParams();
    return;
  }
  try {
    matchGains = await invoke("match_gains_current", {
      reference: refProfile,
      strength: Number($("p-match-strength").value) / 100,
    });
    pushParams();
  } catch (e) {
    setStatus(`Matching failed: ${e}`, "error");
  }
}

$("btn-build-profile").addEventListener("click", async () => {
  const sel = await dialog.open({
    multiple: true,
    title: "Pick good-sounding songs to learn from (2–10 works well)",
    filters: [{ name: "Audio", extensions: AUDIO_EXTS }],
  });
  if (!sel) return;
  const files = Array.isArray(sel) ? sel : [sel];

  const suggested = files.length === 1
    ? files[0].split(/[\\/]/).pop().replace(/\.[^.]+$/, "")
    : `${files.length} songs`;
  const name = await askForName(
    "Name this target sound",
    "Give it a name you'll recognise later — like “Dad's band — studio” or “My vocals 2019”.",
    suggested
  );
  if (name === null) return;

  setStatus(`Listening to ${files.length} song${files.length === 1 ? "" : "s"}…`);
  $("btn-build-profile").disabled = true;
  try {
    refProfile = await invoke("build_profile", { files, name });
    $("p-match-on").checked = true;
    showProfile();
    await refreshMatchGains();
    setStatus(`“${refProfile.name}” is ready — your remasters now aim for that sound.`, "ok");
  } catch (e) {
    setStatus(`Could not learn from those songs: ${e}`, "error");
  } finally {
    $("btn-build-profile").disabled = false;
  }
});

$("btn-rename-profile").addEventListener("click", async () => {
  if (!refProfile) return;
  const name = await askForName(
    "Rename this target sound",
    "This is just a label — it doesn't change how the profile sounds.",
    refProfile.name || ""
  );
  if (name === null) return;
  refProfile.name = name;
  showProfile();
  setStatus(`Renamed to “${name}”.`, "ok");
});

$("btn-save-profile").addEventListener("click", async () => {
  if (!refProfile) return;
  const safe = (refProfile.name || "profile").replace(/[^\w\-. ]+/g, "_");
  const path = await dialog.save({
    title: "Save target sound",
    defaultPath: `${safe}.json`,
    filters: [{ name: "ClearWave profile", extensions: ["json"] }],
  });
  if (!path) return;
  try {
    await invoke("save_profile", { path, reference: refProfile });
    setStatus(`Saved: ${path}`, "ok");
  } catch (e) {
    setStatus(`Could not save profile: ${e}`, "error");
  }
});

$("btn-load-profile").addEventListener("click", async () => {
  const path = await dialog.open({
    title: "Open a saved target sound",
    multiple: false,
    filters: [{ name: "ClearWave profile", extensions: ["json"] }],
  });
  if (!path) return;
  try {
    refProfile = await invoke("load_profile", { path });
    $("p-match-on").checked = true;
    showProfile();
    await refreshMatchGains();
    setStatus(`“${refProfile.name || "Profile"}” loaded.`, "ok");
  } catch (e) {
    setStatus(`Could not open profile: ${e}`, "error");
  }
});

$("btn-clear-profile").addEventListener("click", async () => {
  refProfile = null;
  matchGains = [];
  $("p-match-on").checked = false;
  showProfile();
  pushParams();
  setStatus("Target sound removed.");
});

$("p-match-on").addEventListener("input", refreshMatchGains);
$("p-match-strength").addEventListener("input", () => {
  refreshOutputs();
  clearTimeout(window._matchTimer);
  window._matchTimer = setTimeout(refreshMatchGains, 80);
});

/* ══════════════════════ export ══════════════════════ */

$("btn-out-dir").addEventListener("click", async () => {
  const dir = await dialog.open({ directory: true, title: "Choose where to save" });
  if (dir) {
    outDir = dir;
    $("out-dir").value = dir;
  }
});

$("ai-external").addEventListener("change", () => {
  const mode = $("ai-external").value;
  $("ai-custom-cmd").classList.toggle("hidden", mode !== "custom");
  $("ai-custom-pick").classList.toggle("hidden", mode !== "custom");
  $("voice-fields").classList.toggle("hidden", mode !== "voice");
});

function externalOptions() {
  const mode = $("ai-external").value;
  if (mode === "deepfilter") {
    return { mode: "single", cmd: "deep-filter {in} -o {outdir}", pick: "" };
  }
  if (mode === "crowd") {
    return {
      mode: "single",
      cmd: "audio-separator {in} -m UVR-MDX-NET_Crowd_HQ_1.onnx --output_dir {outdir}",
      pick: "no crowd",
    };
  }
  if (mode === "voice") {
    return {
      mode: "stems",
      separator_cmd: "audio-separator {in} --output_dir {outdir}",
      voice_cmd: $("ai-voice-cmd").value.trim(),
      vocal_gain_db: Number($("ai-vocal-gain").value) || 0,
      instrumental_gain_db: Number($("ai-inst-gain").value) || 0,
    };
  }
  if (mode === "custom") {
    const cmd = $("ai-custom-cmd").value.trim();
    if (!cmd) return null;
    return { mode: "single", cmd, pick: $("ai-custom-pick").value.trim() };
  }
  return null;
}

$("btn-export-one").addEventListener("click", async () => {
  if (currentIndex < 0) return setStatus("Open a track first.", "error");
  const fmt = $("out-format").value;
  const stem = tracks[currentIndex].name.replace(/\.[^.]+$/, "");
  const path = await dialog.save({
    title: "Export this track",
    defaultPath: `${stem} [remastered].${fmt}`,
    filters: [{ name: fmt.toUpperCase(), extensions: [fmt] }],
  });
  if (!path) return;
  setStatus("Rendering… (measuring loudness twice for an exact match)");
  $("btn-export-one").disabled = true;
  try {
    const msg = await invoke("export_track", {
      outputPath: path,
      format: fmt,
      preset: collectPreset(),
      external: externalOptions(),
      reference: $("p-match-on").checked ? refProfile : null,
    });
    setStatus(msg, "ok");
  } catch (e) {
    setStatus(`Export failed: ${e}`, "error");
  } finally {
    $("btn-export-one").disabled = false;
  }
});

$("btn-export-all").addEventListener("click", async () => {
  if (!tracks.length) return setStatus("Add some music first.", "error");
  if (!outDir) return setStatus("Choose a folder to save into first.", "error");
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
      external: externalOptions(),
      reference: $("p-match-on").checked ? refProfile : null,
    });
  } catch (e) {
    setStatus(`Could not start: ${e}`, "error");
    $("btn-export-all").disabled = false;
    $("btn-cancel-batch").classList.add("hidden");
  }
});

$("btn-cancel-batch").addEventListener("click", () => invoke("cancel_batch"));

listen("batch-progress", (ev) => {
  const p = ev.payload;
  if (p.done) {
    $("batch-bar").style.width = "100%";
    $("batch-label").textContent = "All done.";
    $("btn-export-all").disabled = false;
    $("btn-cancel-batch").classList.add("hidden");
    setStatus("✓ Album remastered — every track at the same volume, saved to your folder.", "ok");
    return;
  }
  const frac = batchTotal ? (p.index + (p.stage === "done" ? 1 : 0.5)) / batchTotal : 0;
  $("batch-bar").style.width = `${Math.round(frac * 100)}%`;
  if (p.stage === "processing") {
    $("batch-label").textContent = `(${p.index + 1} of ${p.total}) ${p.file}…`;
  } else if (p.stage === "error") {
    setStatus(`${p.file}: ${p.error}`, "error");
  }
});

/* ══════════════════════ mode switch ══════════════════════ */

function setMode(adv) {
  advanced = adv;
  $("mode-simple").classList.toggle("active", !adv);
  $("mode-advanced").classList.toggle("active", adv);
  $("simple-controls").classList.toggle("hidden", adv);
  $("modules").classList.toggle("hidden", !adv);
  for (const el of document.querySelectorAll(".adv-only")) {
    el.classList.toggle("hidden", !adv);
  }
  if (adv) syncSimpleFromAdvanced();
  else syncSimpleFromAdvanced();
  refreshOutputs();
  drawWave(lastStatus && lastStatus.duration_seconds > 0
    ? lastStatus.position_seconds / lastStatus.duration_seconds : 0);
}

$("mode-simple").addEventListener("click", () => setMode(false));
$("mode-advanced").addEventListener("click", () => setMode(true));

/* ══════════════════════ wiring ══════════════════════ */

$("btn-add-tracks").addEventListener("click", addTracks);
$("btn-welcome-add").addEventListener("click", addTracks);

// Advanced controls drive the engine directly.
for (const el of document.querySelectorAll("#modules input")) {
  el.addEventListener("input", () => {
    syncSimpleFromAdvanced();
    pushParams();
  });
}
// Simple controls map onto the same parameters.
for (const el of document.querySelectorAll("#simple-controls input")) {
  el.addEventListener("input", () => {
    syncAdvancedFromSimple();
    pushParams();
  });
}

async function detectTools() {
  try {
    const t = await invoke("detect_tools");
    const set = (id, on, yes, no) => {
      $(id).classList.toggle("on", on);
      $(id).title = on ? yes : no;
    };
    set("tool-ffmpeg", t.ffmpeg,
      "ffmpeg found — MP3/FLAC/M4A export enabled",
      "ffmpeg not found — install it for MP3/FLAC/M4A (WAV always works)");
    set("tool-deepfilter", t.deepfilter,
      "DeepFilterNet found — stronger noise removal available",
      "DeepFilterNet not found — optional, see the README");
    set("tool-uvr", t.uvr,
      "audio-separator found — crowd removal and vocal rescue available",
      'audio-separator not found — pip install "audio-separator[gpu]" to enable');
    set("tool-demucs", t.demucs,
      "Demucs found — usable as a custom command",
      "Demucs not found — optional, see the README");
    $("format-hint").textContent = t.ffmpeg
      ? "All formats available."
      : "Only WAV is available until ffmpeg is installed (winget install ffmpeg).";
  } catch { /* ignore */ }
}

window.addEventListener("resize", () =>
  drawWave(lastStatus && lastStatus.duration_seconds > 0
    ? lastStatus.position_seconds / lastStatus.duration_seconds : 0));

showProfile();
refreshOutputs();
drawWave(0);
detectTools();
invoke("set_params", { preset: collectPreset() }).catch(() => {});
setInterval(pollStatus, 100);
