"""Master MP3 chapter stitching with FFmpeg concat demuxer and ID3v2.3 tags per Spec 03."""

import os
import subprocess
from typing import List

from mutagen.id3 import ID3, TALB, TCON, TIT2, TPE1, TRCK, ID3NoHeaderError
from mutagen.mp3 import MP3

from tts_service.schemas import StitchChapterMetadata, StitchSegmentItem
from tts_service.silence import ensure_static_silence_files


def stitch_chapter_audio(
    work_id: str,
    chapter_id: str,
    segments: List[StitchSegmentItem],
    metadata: StitchChapterMetadata,
    data_dir: str,
    ffmpeg_bin: str = "ffmpeg",
) -> tuple[str, int, int]:
    """
    Concatenate all segment MP3s with contextual silence buffers into master MP3.
    Injects ID3v2.3 metadata.
    Returns: (master_audio_path: str, file_size_bytes: int, total_duration_ms: int)
    """
    static_dir = os.path.join(data_dir, "audio", "static")
    silence_paths = ensure_static_silence_files(static_dir, ffmpeg_bin=ffmpeg_bin)

    # 1. Verify all segment files exist
    missing_files = [seg.audio_path for seg in segments if not os.path.exists(seg.audio_path)]
    if missing_files:
        raise FileNotFoundError(f"Missing audio segment files: {missing_files}")

    exports_dir = os.path.join(data_dir, "exports")
    os.makedirs(exports_dir, exist_ok=True)
    master_path = os.path.join(exports_dir, f"{work_id}_{chapter_id}_master.mp3")
    temp_master = os.path.join(exports_dir, f".tmp_{work_id}_{chapter_id}_master.mp3")

    # 2. Write concat manifest
    manifest_path = os.path.join(exports_dir, f"concat_{work_id}_{chapter_id}.txt")
    manifest_lines: List[str] = []

    for i, seg in enumerate(segments):
        # Format for FFmpeg: forward slashes
        clean_seg_path = os.path.abspath(seg.audio_path).replace("\\", "/")
        manifest_lines.append(f"file '{clean_seg_path}'")

        # Contextual silence (force last segment pause_after to none)
        is_last = (i == len(segments) - 1)
        if not is_last and seg.pause_after != "none":
            silence_file = silence_paths.get(seg.pause_after)
            if silence_file and os.path.exists(silence_file):
                clean_silence_path = os.path.abspath(silence_file).replace("\\", "/")
                manifest_lines.append(f"file '{clean_silence_path}'")

    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write("\n".join(manifest_lines) + "\n")

    try:
        # 3. Stream-copy concatenation
        cmd = [
            ffmpeg_bin,
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-fflags", "+genpts",
            "-i", manifest_path,
            "-c:a", "copy",
            "-write_xing", "1",
            temp_master,
        ]

        try:
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120.0)
        except subprocess.TimeoutExpired:
            if os.path.exists(temp_master):
                try:
                    os.remove(temp_master)
                except OSError:
                    pass
            raise TimeoutError("FFmpeg concat stitching timed out after 120s")

        if res.returncode != 0:
            if os.path.exists(temp_master):
                try:
                    os.remove(temp_master)
                except OSError:
                    pass
            raise RuntimeError(f"FFmpeg concat stitching failed: {res.stderr.decode(errors='ignore')}")

        os.replace(temp_master, master_path)
    finally:
        if os.path.exists(manifest_path):
            os.remove(manifest_path)

    # 4. Inject ID3v2.3 tags
    audio_file = MP3(master_path)
    try:
        tags = ID3(master_path)
    except ID3NoHeaderError:
        tags = ID3()

    tags.add(TIT2(encoding=3, text=metadata.title))
    tags.add(TPE1(encoding=3, text=metadata.author))
    tags.add(TALB(encoding=3, text=metadata.work_name))
    tags.add(TRCK(encoding=3, text=str(metadata.chapter_number)))
    tags.add(TCON(encoding=3, text="Audiobook"))
    tags.save(master_path, v2_version=3)

    file_size_bytes = os.path.getsize(master_path)
    total_duration_ms = int(audio_file.info.length * 1000)

    return master_path, file_size_bytes, total_duration_ms

