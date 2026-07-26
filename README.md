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
2. Add the album's tracks, open the most problematic one.
3. While it plays: raise **AI Denoise** until the noise is gone (back it off
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
| [Demucs](https://github.com/facebookresearch/demucs) | Splits a track into vocal/drums/bass/other stems | `pip install demucs` |
| [ffmpeg](https://ffmpeg.org) | Enables MP3/FLAC/M4A export | `winget install ffmpeg` |

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
cargo test -p clearwave-engine --no-default-features   # engine tests
tauri dev                                              # run the app
```

## License

Personal project. RNNoise/nnnoiseless (BSD-3), Symphonia (MPL-2.0),
Tauri (MIT/Apache-2.0).
