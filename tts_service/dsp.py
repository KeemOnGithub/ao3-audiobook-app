"""Digital Signal Processing (DSP) and Audio Normalization per Spec 03 section 4."""

import os
import subprocess
import numpy as np
import pyloudnorm as pyln

TARGET_SAMPLE_RATE = 24000
TARGET_LUFS = -16.0
TRUE_PEAK_CEILING_DB = -1.5
LINEAR_CEILING = 10.0 ** (TRUE_PEAK_CEILING_DB / 20.0)  # ~0.841395
MAX_GAIN_LINEAR = 15.85  # +24.0 dB
SHORT_SEGMENT_THRESHOLD_SAMPLES = 9600  # 400ms at 24kHz
TARGET_RMS = 0.12589  # -18.0 dBFS


def validate_audio_content(audio: np.ndarray, orig_sr: int) -> None:
    """Validate that decoded audio contains finite numeric samples, valid sample rate, and nontrivial activity."""
    if orig_sr <= 0:
        raise ValueError(f"INVALID_AUDIO: Invalid sample rate {orig_sr}")
    if audio is None or len(audio) == 0:
        raise ValueError("INVALID_AUDIO: Audio contains zero samples")
    if not np.all(np.isfinite(audio)):
        raise ValueError("INVALID_AUDIO: Audio contains non-finite samples (NaN or Inf)")
    max_amp = np.max(np.abs(audio))
    if max_amp < 1e-4:
        raise ValueError("INVALID_AUDIO: Audio contains no nontrivial signal activity (pure silence)")


def standardize_sample_rate(audio: np.ndarray, orig_sr: int) -> np.ndarray:
    """Ensure audio is 1D float32 mono at 24000 Hz, with DC offset removed."""
    validate_audio_content(audio, orig_sr)

    # Conventional samples x channels downmix over the channel dimension (axis 1 if 2D)
    if audio.ndim > 1:
        channel_axis = 1 if audio.shape[1] <= 2 else 0
        audio = np.mean(audio, axis=channel_axis)

    audio = audio.astype(np.float32)

    if orig_sr != TARGET_SAMPLE_RATE:
        # Resample using linear interpolation
        num_target_samples = int(round(len(audio) * TARGET_SAMPLE_RATE / orig_sr))
        x_orig = np.linspace(0, 1, len(audio), endpoint=False)
        x_target = np.linspace(0, 1, num_target_samples, endpoint=False)
        audio = np.interp(x_target, x_orig, audio).astype(np.float32)

    # Remove DC offset
    audio = audio - np.mean(audio)
    return audio


def trim_and_apply_fade(audio: np.ndarray) -> np.ndarray:
    """
    Trim leading and trailing silence below -50.0 dBFS (10ms window, 5ms hop).
    Apply 40ms Hann de-click fade-in and fade-out.
    """
    window_size = 240  # 10ms at 24kHz
    hop_size = 120     # 5ms at 24kHz

    if len(audio) < window_size:
        raise ValueError("INVALID_AUDIO: Audio shorter than window size")

    # Compute frame-wise RMS
    num_frames = (len(audio) - window_size) // hop_size + 1
    rms_values = np.zeros(num_frames, dtype=np.float32)
    for i in range(num_frames):
        start = i * hop_size
        frame = audio[start : start + window_size]
        rms_values[i] = np.sqrt(np.mean(frame ** 2))

    dbfs = 20.0 * np.log10(np.maximum(rms_values, 1e-9))
    active_frames = np.where(dbfs > -50.0)[0]

    # Guard: if model produced pure silence below -50dBFS, fail closed per Spec 07
    if len(active_frames) == 0:
        raise ValueError("INVALID_AUDIO: No signal activity above -50.0 dBFS threshold")

    start_sample = max(0, int(active_frames[0] * hop_size) - window_size)
    end_sample = min(len(audio), int(active_frames[-1] * hop_size + window_size) + window_size)
    trimmed = audio[start_sample:end_sample].copy()

    if len(trimmed) == 0:
        raise ValueError("INVALID_AUDIO: Audio trimmed to zero length")

    # Apply 40ms Hann fade envelope
    fade_len = 960  # 40ms at 24kHz
    if len(trimmed) < 2880:  # < 120ms
        fade_len = max(len(trimmed) // 4, 1)

    fade_in = 0.5 * (1.0 - np.cos(np.pi * np.arange(fade_len) / fade_len))
    fade_out = 0.5 * (1.0 + np.cos(np.pi * np.arange(fade_len) / fade_len))

    trimmed[:fade_len] *= fade_in
    trimmed[-fade_len:] *= fade_out

    return trimmed


def normalize_loudness(audio: np.ndarray) -> np.ndarray:
    """
    Normalize loudness to -16.0 LUFS (EBU R128) with short-segment fallback (<400ms)
    and true peak limiting capping strictly at -1.5 dBFS.
    """
    if len(audio) == 0 or np.all(audio == 0):
        return audio

    if len(audio) >= SHORT_SEGMENT_THRESHOLD_SAMPLES:
        # Case A: Duration >= 400ms
        meter = pyln.Meter(TARGET_SAMPLE_RATE)
        try:
            loudness = meter.integrated_loudness(audio)
        except Exception:
            loudness = float("-inf")

        if np.isfinite(loudness) and loudness > -70.0:
            gain = 10.0 ** ((TARGET_LUFS - loudness) / 20.0)
            gain = min(gain, MAX_GAIN_LINEAR)
            audio = audio * gain
        else:
            # Fallback to Case B
            rms = np.sqrt(np.mean(audio ** 2))
            gain = min(TARGET_RMS / max(rms, 1e-6), MAX_GAIN_LINEAR)
            audio = audio * gain
    else:
        # Case B: Duration < 400ms
        rms = np.sqrt(np.mean(audio ** 2))
        gain = min(TARGET_RMS / max(rms, 1e-6), MAX_GAIN_LINEAR)
        audio = audio * gain

    # Peak Limiting
    peak = np.max(np.abs(audio))
    if peak > LINEAR_CEILING:
        audio = audio * (LINEAR_CEILING / peak)

    return audio.astype(np.float32)


def process_audio_pipeline(audio: np.ndarray, orig_sr: int = 24000) -> np.ndarray:
    """Execute complete deterministic DSP pipeline."""
    standardized = standardize_sample_rate(audio, orig_sr)
    trimmed = trim_and_apply_fade(standardized)
    normalized = normalize_loudness(trimmed)
    return normalized


def encode_f32_to_mp3(
    audio: np.ndarray,
    output_path: str,
    ffmpeg_bin: str = "ffmpeg",
    write_xing: int = 0,
    id3_version: int = 0,
) -> None:
    """
    Deterministic MP3 encoding via FFmpeg pipe:
    24000 Hz, mono, 128 kbps CBR, with controllable Xing header.
    Individual segments use write_xing=0, id3_version=0 for clean concat.
    """
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    temp_path = f"{output_path}.tmp.mp3"

    cmd = [
        ffmpeg_bin,
        "-y",
        "-f", "f32le",
        "-ar", str(TARGET_SAMPLE_RATE),
        "-ac", "1",
        "-i", "pipe:0",
        "-c:a", "libmp3lame",
        "-b:a", "128k",
        "-cbr_quality", "0",
        "-ar", str(TARGET_SAMPLE_RATE),
        "-ac", "1",
        "-write_xing", str(write_xing),
        "-id3v2_version", str(id3_version),
        temp_path,
    ]

    p = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        _, stderr = p.communicate(input=audio.astype(np.float32).tobytes(), timeout=60.0)
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise TimeoutError("FFmpeg MP3 encoding timed out after 60s")

    if p.returncode != 0:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise RuntimeError(f"FFmpeg MP3 encoding failed: {stderr.decode(errors='ignore')}")

    os.replace(temp_path, output_path)

