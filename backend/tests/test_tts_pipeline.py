"""Unit and integration tests for Phase 3: TTS Synthesis & Audio Pipeline."""

import os
import shutil
import tempfile
import numpy as np
import pytest
import httpx
from mutagen.mp3 import MP3
from mutagen.id3 import ID3

from tts_service.app import app
from tts_service import app as tts_app_module
from tts_service.models import TTSModelManager
from tts_service.dsp import (
    normalize_loudness,
    process_audio_pipeline,
    standardize_sample_rate,
    trim_and_apply_fade,
    encode_f32_to_mp3,
)
from tts_service.silence import ensure_static_silence_files
from tts_service.stitcher import stitch_chapter_audio
from tts_service.schemas import StitchChapterMetadata, StitchSegmentItem


@pytest.fixture
def temp_data_dir():
    d = tempfile.mkdtemp(prefix="tts_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_dsp_sample_rate_and_dc_offset():
    """Verify sample rate standardization and DC offset removal."""
    sr_orig = 16000
    t = np.linspace(0, 1.0, 16000, endpoint=False)
    # Signal with +0.5 DC bias
    raw_signal = (np.sin(2 * np.pi * 440 * t) + 0.5).astype(np.float32)

    processed = standardize_sample_rate(raw_signal, sr_orig)
    assert len(processed) == 24000
    assert abs(np.mean(processed)) < 1e-4


def test_dsp_trim_and_fade():
    """Verify silence trimming and Hann fade envelope application."""
    sr = 24000
    # 0.5s silence + 0.5s tone + 0.5s silence
    silence = np.zeros(12000, dtype=np.float32)
    tone = (0.5 * np.sin(2 * np.pi * 440 * np.linspace(0, 0.5, 12000))).astype(np.float32)
    signal = np.concatenate([silence, tone, silence])

    trimmed = trim_and_apply_fade(signal)
    # Should have removed large parts of leading/trailing silence
    assert len(trimmed) < len(signal)
    # Check de-click envelope: first sample close to 0
    assert abs(trimmed[0]) < 1e-3
    assert abs(trimmed[-1]) < 1e-3


def test_dsp_loudness_normalization_long_segment():
    """Verify -16.0 LUFS loudness normalization on segment >= 400ms."""
    sr = 24000
    t = np.linspace(0, 1.0, 24000)
    audio = (0.2 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)

    normalized = normalize_loudness(audio)
    peak = np.max(np.abs(normalized))
    assert peak <= 0.841395 + 1e-3  # Capped at -1.5 dBFS ceiling


def test_dsp_loudness_short_segment_fallback():
    """Verify <400ms segment falls back to RMS normalization without crashing."""
    # 200ms audio (< 9600 samples)
    audio = (0.1 * np.sin(2 * np.pi * 440 * np.linspace(0, 0.2, 4800))).astype(np.float32)
    normalized = normalize_loudness(audio)
    peak = np.max(np.abs(normalized))
    assert peak <= 0.841395 + 1e-3


def test_encode_f32_to_mp3(temp_data_dir):
    """Verify deterministic MP3 encoding via FFmpeg pipe produces valid playable MP3."""
    audio = (0.3 * np.sin(2 * np.pi * 440 * np.linspace(0, 1.0, 24000))).astype(np.float32)
    out_mp3 = os.path.join(temp_data_dir, "test.mp3")

    encode_f32_to_mp3(audio, out_mp3)
    assert os.path.exists(out_mp3)

    mp3_info = MP3(out_mp3)
    assert mp3_info.info.sample_rate == 24000
    assert mp3_info.info.bitrate == 128000
    assert 0.9 <= mp3_info.info.length <= 1.1


def test_static_silence_generation(temp_data_dir):
    """Verify 3 static pause files are generated with exact durations."""
    static_dir = os.path.join(temp_data_dir, "audio", "static")
    paths = ensure_static_silence_files(static_dir)

    for key, path in paths.items():
        assert os.path.exists(path)
        info = MP3(path).info
        assert info.sample_rate == 24000

    # Intra paragraph ~250ms
    assert 0.2 <= MP3(paths["intra_paragraph"]).info.length <= 0.35
    # Paragraph end ~650ms
    assert 0.6 <= MP3(paths["paragraph_end"]).info.length <= 0.75
    # Scene break ~1200ms
    assert 1.1 <= MP3(paths["scene_break"]).info.length <= 1.3


@pytest.mark.asyncio
async def test_tts_service_endpoints(temp_data_dir):
    """Verify all FastAPI endpoints in tts_service."""
    app.state.data_dir = temp_data_dir
    tts_app_module.model_manager = TTSModelManager(use_mock=True)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Health
        health_res = await client.get("/internal/tts/health")
        assert health_res.status_code == 200
        assert health_res.json()["status"] == "ready"

        # 2. Create Voice
        create_res = await client.post(
            "/internal/tts/create-voice",
            json={
                "work_id": "work123",
                "character_id": "char_bob",
                "version": 1,
                "voice_instruct": "An adult male warm baritone with steady delivery.",
                "reference_sample_text": "This is a reference text that is long enough to clone a voice.",
            },
        )
        assert create_res.status_code == 200
        voice_data = create_res.json()
        assert os.path.exists(voice_data["embedding_path"])
        assert os.path.exists(voice_data["preview_audio_path"])

        # 3. Synthesize Line
        synth_res = await client.post(
            "/internal/tts/synthesize-line",
            json={
                "chapter_id": "chap456",
                "segment_id": "seg_00001",
                "order": 1,
                "version": 1,
                "text": "Hello world from our synthesized audiobook line.",
                "embedding_path": voice_data["embedding_path"],
            },
        )
        assert synth_res.status_code == 200
        synth_data = synth_res.json()
        seg1_path = synth_data["audio_path"]
        assert os.path.exists(seg1_path)

        # Synthesize second segment
        synth_res2 = await client.post(
            "/internal/tts/synthesize-line",
            json={
                "chapter_id": "chap456",
                "segment_id": "seg_00002",
                "order": 2,
                "version": 1,
                "text": "Second line concluding the short chapter.",
                "embedding_path": voice_data["embedding_path"],
            },
        )
        assert synth_res2.status_code == 200
        seg2_path = synth_res2.json()["audio_path"]

        # 4. Stitch Chapter
        stitch_res = await client.post(
            "/internal/tts/stitch-chapter",
            json={
                "work_id": "work123",
                "chapter_id": "chap456",
                "segments": [
                    {"audio_path": seg1_path, "pause_after": "intra_paragraph"},
                    {"audio_path": seg2_path, "pause_after": "none"},
                ],
                "metadata": {
                    "title": "Chapter 1: The Outset",
                    "author": "Alice Author",
                    "work_name": "Epic Fanfic",
                    "chapter_number": 1,
                },
            },
        )
        assert stitch_res.status_code == 200
        stitch_data = stitch_res.json()
        master_path = stitch_data["master_audio_path"]
        assert os.path.exists(master_path)

        # Verify ID3 Tags
        tags = ID3(master_path)
        assert tags.get("TIT2").text[0] == "Chapter 1: The Outset"
        assert tags.get("TPE1").text[0] == "Alice Author"
        assert tags.get("TALB").text[0] == "Epic Fanfic"
        assert tags.get("TRCK").text[0] == "1"
        assert tags.get("TCON").text[0] == "Audiobook"


def test_missing_remote_url_fails_in_remote_mode():
    """Verify that a missing remote URL in remote mode raises a configuration error."""
    with pytest.raises(ValueError, match="CONFIGURATION_ERROR"):
        TTSModelManager(use_mock=False, remote_url="")


def test_audio_validation_rejects_empty_nan_and_silence():
    """Verify that empty, NaN, and pure silent buffers are rejected per Spec 07 section 7.3."""
    from tts_service.dsp import validate_audio_content

    # Zero samples
    with pytest.raises(ValueError, match="zero samples"):
        validate_audio_content(np.array([], dtype=np.float32), 24000)

    # Invalid sample rate
    with pytest.raises(ValueError, match="Invalid sample rate"):
        validate_audio_content(np.array([0.1, -0.1], dtype=np.float32), 0)

    # NaN / Inf
    with pytest.raises(ValueError, match="non-finite"):
        validate_audio_content(np.array([0.1, np.nan, -0.1], dtype=np.float32), 24000)

    # Pure silence
    with pytest.raises(ValueError, match="no nontrivial signal activity"):
        validate_audio_content(np.zeros(24000, dtype=np.float32), 24000)


def test_dsp_stereo_and_mono_downmix():
    """Verify samples x channels conventional stereo downmix over channel axis."""
    sr = 24000
    t = np.linspace(0, 0.5, 12000, endpoint=False)
    ch1 = 0.4 * np.sin(2 * np.pi * 440 * t)
    ch2 = 0.2 * np.sin(2 * np.pi * 440 * t)
    stereo = np.column_stack([ch1, ch2]).astype(np.float32)

    mono = standardize_sample_rate(stereo, sr)
    assert mono.ndim == 1
    assert len(mono) == 12000
    # Peak amplitude should be roughly average of ch1 and ch2 peaks
    assert abs(np.max(mono) - 0.3) < 0.05


@pytest.mark.asyncio
async def test_preview_cap_preserves_full_conditioning_and_narration(tmp_path,monkeypatch):
    from unittest.mock import Mock
    import json
    manager = TTSModelManager(use_mock=True)
    waveform = np.sin(np.arange(20 * 24000,dtype=np.float32) / 12) * .2
    manager.generate_voice_design = Mock(return_value=(waveform,24000))
    manager.generate_voice_clone = Mock(return_value=(waveform,24000))
    monkeypatch.setattr(tts_app_module,'model_manager',manager)
    monkeypatch.setattr(tts_app_module,'process_audio_pipeline',lambda wav,sr:wav)
    encoded = []
    def encode(audio,output_path,**kwargs):
        encoded.append(len(audio))
        with open(output_path,'wb') as stream:
            stream.write(b'encoded')
    monkeypatch.setattr(tts_app_module,'encode_f32_to_mp3',encode)
    app.state.data_dir = str(tmp_path)
    sample = 'This complete reference sample must remain in the conditioning artifact.'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        result = await client.post('/internal/tts/create-voice',json={'work_id':'w','character_id':'candidate_unique',
            'voice_instruct':'An expressive warm voice.','reference_sample_text':sample,'allow_voice_recreation':False})
        assert result.status_code == 200
        assert result.json()['duration_ms'] == 15000 and encoded == [15 * 24000]
        with open(result.json()['embedding_path'],'rb') as stream:
            metadata = json.loads(stream.read())
        assert metadata['sample_text'] == sample and metadata['allow_recreation'] is False
        result = await client.post('/internal/tts/synthesize-line',json={'chapter_id':'c','segment_id':'s','order':0,
            'text':'Exact narration.','embedding_path':result.json()['embedding_path']})
        assert result.status_code == 200 and result.json()['duration_ms'] == 20000
        assert encoded == [15 * 24000,20 * 24000]


