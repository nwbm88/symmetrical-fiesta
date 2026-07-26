# ClearWave 🎛️

**Restore & remaster rough recordings — entirely on your own PC.**

ClearWave is a Windows desktop app for cleaning up badly recorded music
(YouTube rips, old live recordings, low-quality transfers). Load a track,
hear your changes **live** while you tweak, get it sounding right, then
apply the exact same recipe to the whole album in one click — with every
track normalized to the same loudness so there are no volume jumps.

Nothing is uploaded anywhere. The AI denoising is a neural network that
runs locally inside the app.

## Features

- **✨ Auto mode** — one click analyzes the track (noise floor + SNR,
  9-band spectral balance, dynamic spread) and sets every module with a
  plain-English explanation of each decision. Use it as the starting point,
  then fine-tune by ear.
- **Reference profiles** — feed ClearWave a few clean studio tracks (any
  artist whose sound you want, including your own band's good takes) and it
  fingerprints their tonal balance into a 24-band profile. A matching EQ
  then morphs every remaster toward that sound — live while you listen, and
  recomputed per-track during batch so the whole album converges on the same
  target. Profiles save/load as JSON.
- **AI crowd removal** — one-click integration with UVR's dedicated
  crowd-separation model via `audio-separator` (see below): strips audience
  noise from live recordings before the rest of the chain runs.
- **Stem Rescue** — separates the vocal from the band, runs the vocal
  through a voice tool of your choice (a model of your own voice, or a
  no-training enhancer), and remixes with level trims. See
  [Voice profiles & Stem Rescue](#voice-profiles--stem-rescue).
- **Live preview** — real-time DSP engine; every slider is heard instantly
  while the track plays. Press `Space` to play/pause, click the waveform to
  seek, and use **A/B Original** to compare against the untouched file.
- **AI Denoise (built in, local)** — the RNNoise recurrent neural network
  removes hiss and background noise. It's precomputed when a track loads, so
  the *Amount* slider is a zero-latency dry/wet blend you can ride in real time.
- **Rumble filter** — 24 dB/oct high-pass for mic thumps and sub-bass mud.
- **5-band EQ** — low shelf, three parametric peaks, high shelf.
- **Compressor** — soft-knee, stereo-linked, with gain-reduction metering.
- **Loudness normalization (EBU R128)** — set a target LUFS once; every
  exported track hits it exactly (two-pass measurement at export). This is
  what fixes "crazy volume jumps" across an album.
- **Brick-wall limiter** — nothing ever clips past the ceiling.
- **Presets** — save your recipe as JSON, load it any time.
- **Batch remaster** — add the whole album, pick an output folder, one click.
  Progress is mirrored onto the track list, so a failed track is obvious
  (hover it for the reason).
- **Simple and Advanced modes** — Simple gives you plain-language controls
  (Remove noise & hiss / Tone / Even out the volume); Advanced opens the full
  EQ, compressor and limiter. Both drive the same engine.
- **Drag and drop** files straight onto the window, and ClearWave remembers
  your output folder, format, mode and target sound between sessions.
- **Formats** — reads MP3/M4A/AAC/FLAC/WAV/OGG/Opus/WebM and more; exports
  24-bit WAV always, plus MP3/FLAC/M4A when `ffmpeg` is installed.

## Getting the app

Every push builds a Windows installer on GitHub Actions:

1. Open the repo's **Actions** tab → latest **Build ClearWave** run.
2. Download the **ClearWave-windows-installer** artifact and run the `.exe`.

Or build locally on Windows (needs [Rust](https://rustup.rs) and Node):

```powershell
npm install -g @tauri-apps/cli
tauri build
# installer lands in src-tauri/target/release/bundle/nsis/
```

## Suggested workflow

1. **Get the best source first.** If the audio came from YouTube, rip with
   `yt-dlp -f bestaudio` — a better source beats any amount of repair.
2. Add the album's tracks, open the most problematic one, hit **✨ Auto
   settings**, and listen to what it chose (the notes under the waveform
   explain every decision).
3. Then fine-tune while it plays: raise **AI Denoise** until the noise is gone (back it off
   if the music starts sounding underwater — 30–70% is the sweet spot).
4. Set the **Rumble filter** around 30–60 Hz; use the **EQ** to fix tone
   (muffled → high shelf up; harsh → cut around 2–4 kHz; boomy → cut 150–300 Hz).
5. Add gentle **Compressor** (2–3:1, threshold just into the music) if the
   track itself lurches between loud and quiet.
6. Pick a **Loudness** target: `-14 LUFS` matches streaming loudness;
   use `-16` to `-18` for a more dynamic feel.
7. **A/B Original** often — your ears adjust fast.
8. Save the preset, choose an output folder, **Remaster whole album**.

## Optional external AI tools (heavier cleanup)

The built-in denoiser is fast and always available. For tougher material you
can chain in bigger local AI models — ClearWave runs them as a pre-processing
stage during export/batch (the *Deep clean* option). Detected tools show as
green badges in the title bar.

| Tool | What it does | Install |
|------|--------------|---------|
| [DeepFilterNet](https://github.com/Rikorose/DeepFilterNet) | Stronger neural noise removal | download `deep-filter` binary onto your PATH |
| [audio-separator](https://github.com/nomadkaraoke/python-audio-separator) (UVR models) | **Crowd removal**, vocal/instrument separation | `pip install "audio-separator[gpu]"` |
| [Demucs](https://github.com/facebookresearch/demucs) | Splits a track into vocal/drums/bass/other stems | `pip install demucs` |
| [Applio](https://github.com/IAHispano/Applio) / RVC | Trains a reusable voice model (see Stem Rescue) | download the app |
| [resemble-enhance](https://github.com/resemble-ai/resemble-enhance) | Restores a vocal stem, no training needed | `pip install resemble-enhance` |
| [ffmpeg](https://ffmpeg.org) | Enables MP3/FLAC/M4A export | `winget install ffmpeg` |

**Crowd removal:** select *Deep clean → Crowd removal (UVR)*. This runs the
`UVR-MDX-NET_Crowd_HQ_1` model (auto-downloaded on first use by
audio-separator) and keeps the "No Crowd" stem. On an RTX 2060 Super this
processes a few times faster than real time. Expect startlingly good results
on steady crowd rumble/chatter; whistles right next to the taper are harder.

- **DeepFilterNet**: select *Deep clean → DeepFilterNet* and it's used
  automatically during export.
- **Any other tool** (including Demucs recipes): select *Deep clean → Custom
  command* and give a command template using `{in}` and `{out}` (or
  `{outdir}`) placeholders, e.g.:

  ```
  my-enhancer "{in}" --output "{out}"
  ```

All of this stays on your machine — for personal-use restoration of your own
recordings there's no cloud upload and no service terms involved.

**GPU note:** the app itself (including the built-in AI denoise) runs on CPU
and uses negligible resources — no GPU settings needed. Of the external
tools, only Demucs uses your GPU (CUDA), and any 6 GB+ card handles it.

**Which denoiser when?** RNNoise (built in) is mild and safe: excellent on
hiss and broadband noise, and it deliberately leaves anything speech-like
(including crowd chatter) alone. DeepFilterNet is much stronger on tough
noise. For crowd noise use the UVR crowd model; for wrecked recordings,
Demucs stem separation is the nuclear option.

## Voice profiles & Stem Rescue

There are two different things people mean by "make a profile of the singer",
and ClearWave supports both — one built in, one by orchestrating a trainer.

### 1. Tonal profile — built in, no training

This is the **Reference profile** feature above. Give it your studio
recordings and it learns their *sonic fingerprint* (how the voice and band
sit across 24 frequency bands) and pulls rough recordings toward that sound.
No model training, works in seconds, and it's the right tool ~80% of the time.

### 2. Voice model — train once, then use Stem Rescue

A true voice model (learning the timbre of a specific singer, so a damaged
vocal can be re-rendered in that voice) has to be *trained*, and there are
mature open-source trainers for it. ClearWave doesn't reimplement them —
it orchestrates the pipeline that makes one usable for remastering:

**Step 1 — train a model of the voice (once).** Use
[Applio](https://github.com/IAHispano/Applio) (a friendly RVC front-end).
Feed it ~10+ minutes of clean, isolated vocals of the singer. On an RTX 2060
Super this is an evening's work, and the model is reusable forever.

**Step 2 — use it in ClearWave.** Select *Deep clean → **Stem rescue***.
For every track, ClearWave then:

1. separates the vocal from the band (`audio-separator`),
2. runs **only the vocal** through your voice tool,
3. remixes vocal + band (with individual level trims),
4. and continues into the normal chain (denoise → EQ → loudness → limiter).

Put your model's CLI in the *voice tool* box using `{in}` / `{out}`
placeholders. No voice model? A no-training vocal restorer works in the same
slot and is a great first experiment:

```
resemble-enhance "{in}" "{out}"
```

Stem rescue is also just a good way to work even without any voice tool —
leave the voice box empty and use the vocal/band trims to rebalance a mix
where the singer is buried.

### Please only model voices you have the right to

Use this for **your own voice, your band, or people who have given you
permission** — which is exactly the case you described. A voice model is
personal to the singer: don't build one of an artist you don't know, and
don't publish output that could be taken for a real recording by someone who
didn't consent. Everything here runs locally, so this is on your honour
rather than enforced by a service.

**Other restoration for your own recordings:** reference profiles work
especially well on family tapes — build the profile from the best-sounding
recordings and match the rough ones to them.

## Headless testing

The whole pipeline runs from the command line too:

```bash
cargo run --release -p clearwave-engine --example remaster -- input.mp3 out.wav
```

It prints the auto-analysis report (noise floor, SNR, band levels, chosen
settings) and before/after loudness, peak and noise-floor measurements.

## Architecture

```
engine/     Rust audio engine (no UI): symphonia decode → rubato resample
            → RNNoise denoise twin → biquad HPF/EQ → compressor →
            EBU R128 loudness → limiter → 24-bit WAV / ffmpeg encode.
            Real-time playback via cpal; parameters are lock-free atomics
            so the audio thread never blocks.
src-tauri/  Tauri v2 desktop shell: commands, batch worker, progress events.
ui/         Vanilla HTML/CSS/JS front end (no build step): waveform,
            transport, module cards, batch queue.
```

The offline render is *exact*: it re-runs the chain, measures the result,
and applies the precise gain to hit the loudness target before the final
limiter pass — so the whole album lands at identical loudness.

## Development

```bash
cargo test -p clearwave-engine --no-default-features   # engine tests (18)
cd ui-tests && npm install && npm test                 # UI smoke tests (11)
tauri dev                                              # run the app
```

The UI tests load the real front end in Chromium against a mock Tauri
backend (`ui-tests/mock-tauri.js`) and drive the actual flows — adding and
removing tracks, batch progress, the Simple ⇄ Advanced mapping, dialogs and
saved preferences. Both suites run in CI before the Windows installer builds.

### Verifying drag and drop headlessly

Drag and drop needs a real webview, so it can't be covered by the Chromium
suite. The `test-hooks` feature (never compiled into a shipping build) injects
the same `tauri://drag-*` event sequence the OS emits:

```bash
cargo build -p clearwave --release --features test-hooks
Xvfb :99 -screen 0 1400x900x24 &
DISPLAY=:99 CLEARWAVE_TEST_DROP="/path/a.mp3,/path/b.mp3" \
  ./target/release/clearwave
DISPLAY=:99 import -window root shot.png
```

## License

Personal project. RNNoise/nnnoiseless (BSD-3), Symphonia (MPL-2.0),
Tauri (MIT/Apache-2.0).
