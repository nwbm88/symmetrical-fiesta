use std::fs::File;
use std::path::Path;

use anyhow::{anyhow, Context, Result};
use symphonia::core::audio::SampleBuffer;
use symphonia::core::codecs::{DecoderOptions, CODEC_TYPE_NULL};
use symphonia::core::errors::Error as SymphoniaError;
use symphonia::core::formats::FormatOptions;
use symphonia::core::io::MediaSourceStream;
use symphonia::core::meta::MetadataOptions;
use symphonia::core::probe::Hint;

use crate::AudioData;

/// Decode any supported audio file (mp3, aac/m4a, flac, wav, ogg, opus, ...)
/// into interleaved stereo f32 at the file's native sample rate.
pub fn decode_file(path: &Path) -> Result<AudioData> {
    let file = File::open(path).with_context(|| format!("opening {}", path.display()))?;
    let mss = MediaSourceStream::new(Box::new(file), Default::default());

    let mut hint = Hint::new();
    if let Some(ext) = path.extension().and_then(|e| e.to_str()) {
        hint.with_extension(ext);
    }

    let probed = symphonia::default::get_probe()
        .format(
            &hint,
            mss,
            &FormatOptions::default(),
            &MetadataOptions::default(),
        )
        .context("unrecognized or unsupported audio format")?;
    let mut format = probed.format;

    let track = format
        .tracks()
        .iter()
        .find(|t| t.codec_params.codec != CODEC_TYPE_NULL)
        .ok_or_else(|| anyhow!("no decodable audio track found"))?;
    let track_id = track.id;

    let mut decoder = symphonia::default::get_codecs()
        .make(&track.codec_params, &DecoderOptions::default())
        .context("failed to create decoder for this codec")?;

    let mut sample_rate = track.codec_params.sample_rate.unwrap_or(44_100);
    let mut samples: Vec<f32> = Vec::new();
    let mut sbuf: Option<SampleBuffer<f32>> = None;

    loop {
        let packet = match format.next_packet() {
            Ok(p) => p,
            Err(SymphoniaError::IoError(e))
                if e.kind() == std::io::ErrorKind::UnexpectedEof =>
            {
                break;
            }
            Err(SymphoniaError::ResetRequired) => break,
            Err(e) => return Err(e.into()),
        };
        if packet.track_id() != track_id {
            continue;
        }
        match decoder.decode(&packet) {
            Ok(audio_buf) => {
                let spec = *audio_buf.spec();
                sample_rate = spec.rate;
                let channels = spec.channels.count();
                let needed = audio_buf.capacity() as u64;
                let realloc = match &sbuf {
                    Some(b) => b.capacity() < needed as usize * channels,
                    None => true,
                };
                if realloc {
                    sbuf = Some(SampleBuffer::<f32>::new(needed, spec));
                }
                let b = sbuf.as_mut().unwrap();
                b.copy_interleaved_ref(audio_buf);
                push_as_stereo(&mut samples, b.samples(), channels);
            }
            // Recoverable bitstream error (common in web rips): skip the packet.
            Err(SymphoniaError::DecodeError(_)) => continue,
            Err(e) => return Err(e.into()),
        }
    }

    if samples.is_empty() {
        return Err(anyhow!("file decoded to zero audio frames"));
    }

    Ok(AudioData {
        sample_rate,
        samples,
    })
}

fn push_as_stereo(out: &mut Vec<f32>, interleaved: &[f32], channels: usize) {
    match channels {
        0 => {}
        1 => {
            out.reserve(interleaved.len() * 2);
            for &s in interleaved {
                out.push(s);
                out.push(s);
            }
        }
        2 => out.extend_from_slice(interleaved),
        n => {
            // Multichannel: keep the front left/right pair.
            out.reserve(interleaved.len() / n * 2);
            for frame in interleaved.chunks_exact(n) {
                out.push(frame[0]);
                out.push(frame[1]);
            }
        }
    }
}
