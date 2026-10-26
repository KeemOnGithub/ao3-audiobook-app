"""Static pause MP3 generation per Spec 03 section 5.1."""

import os
import numpy as np
from tts_service.dsp import TARGET_SAMPLE_RATE, encode_f32_to_mp3

STATIC_SILENCE_CONFIGS = {
    "intra_paragraph": ("silence_intra_paragraph.mp3", 250, 6000),
    "paragraph_end": ("silence_paragraph_end.mp3", 650, 15600),
    "scene_break": ("silence_scene_break.mp3", 1200, 28800),
}


def ensure_static_silence_files(static_dir: str, ffmpeg_bin: str = "ffmpeg") -> dict[str, str]:
    """
    Ensure the three static pause MP3 files exist in static_dir.
    Generated once from zeroed f32le buffers with exact CBR encoding parameters.
    """
    os.makedirs(static_dir, exist_ok=True)
    paths: dict[str, str] = {}

    for key, (filename, duration_ms, num_samples) in STATIC_SILENCE_CONFIGS.items():
        file_path = os.path.join(static_dir, filename)
        paths[key] = file_path
        if not os.path.exists(file_path):
            zero_buffer = np.zeros(num_samples, dtype=np.float32)
            encode_f32_to_mp3(
                audio=zero_buffer,
                output_path=file_path,
                ffmpeg_bin=ffmpeg_bin,
                write_xing=0,
                id3_version=0,
            )

    return paths

