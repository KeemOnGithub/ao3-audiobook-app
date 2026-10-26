"""FastAPI application for Qwen3-TTS microservice per Spec 03."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import os
import re
import hashlib
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse

from tts_service.dsp import encode_f32_to_mp3, process_audio_pipeline
from tts_service.models import TTSModelManager
from tts_service.schemas import (
    CreateVoiceRequest,
    CreateVoiceResponse,
    StitchChapterRequest,
    StitchChapterResponse,
    SynthesizeLineRequest,
    SynthesizeLineResponse,
    TTSHealthResponse,
)
from tts_service.silence import ensure_static_silence_files
from tts_service.stitcher import stitch_chapter_audio

DEFAULT_DATA_DIR = os.getenv("DATA_DIR", "./data")
DEFAULT_FFMPEG_BIN = os.getenv("FFMPEG_BINARY", "ffmpeg")

# Global lock and single-worker executor for GPU/DSP serialization
gpu_lock = asyncio.Lock()
gpu_executor = ThreadPoolExecutor(max_workers=1)
model_manager: Optional[TTSModelManager] = None


def get_model_manager() -> TTSModelManager:
    global model_manager
    if model_manager is None:
        use_mock = os.getenv("TTS_USE_MOCK", "").lower() in ("true", "1")
        remote_url = os.getenv("REMOTE_TTS_URL")
        model_manager = TTSModelManager(
            use_mock=use_mock,
            remote_url=remote_url,
            api_key=os.getenv("TTS_API_KEY", "dummy"),
        )
    return model_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    mgr = get_model_manager()
    data_dir = getattr(app.state, "data_dir", DEFAULT_DATA_DIR)
    ffmpeg_bin = getattr(app.state, "ffmpeg_bin", DEFAULT_FFMPEG_BIN)

    # 1. Ensure all directories exist
    os.makedirs(os.path.join(data_dir, "voices"), exist_ok=True)
    os.makedirs(os.path.join(data_dir, "previews"), exist_ok=True)
    os.makedirs(os.path.join(data_dir, "audio", "static"), exist_ok=True)
    os.makedirs(os.path.join(data_dir, "exports"), exist_ok=True)

    # 2. Pre-bake static silence files
    static_dir = os.path.join(data_dir, "audio", "static")
    ensure_static_silence_files(static_dir, ffmpeg_bin=ffmpeg_bin)

    # 3. Pre-bake system_narrator_v1.pt
    narrator_pt_path = os.path.join(data_dir, "voices", "system_narrator_v1.pt")
    if not os.path.exists(narrator_pt_path):
        prompt_bytes = mgr.create_voice_clone_prompt(
            "dummy",
            "The shadows lengthened across the quiet room as the evening wind began to rise outside.",
            voice_id="system_narrator_v1",
            instruct="An adult neutral narrator voice, warm mid-register pitch, clear and articulate studio audiobook delivery, steady and engaging pacing with natural storytelling inflection.",
            language="English",
        )
        with open(narrator_pt_path, "wb") as f:
            f.write(prompt_bytes)

    yield

    gpu_executor.shutdown(wait=False)


app = FastAPI(title="Qwen3-TTS Pipeline Service", lifespan=lifespan)
app.state.data_dir = DEFAULT_DATA_DIR
app.state.ffmpeg_bin = DEFAULT_FFMPEG_BIN


@app.get("/internal/tts/health", response_model=TTSHealthResponse)
async def tts_health():
    mgr = get_model_manager()
    data_dir = app.state.data_dir
    narrator_path = os.path.join(data_dir, "voices", "system_narrator_v1.pt")
    
    redacted_url = None
    if mgr.remote_url:
        redacted_url = "https://***" if "://" in mgr.remote_url else "remote"

    status_str = "ready"
    if not mgr.use_mock and not mgr.remote_url:
        status_str = "unavailable"

    return TTSHealthResponse(
        status=status_str,
        provider_mode=mgr.provider_mode,
        provider_base_url=redacted_url,
        capabilities={"voice_create": True, "speak": True, "stitch": True},
        vram_allocated_mb=0.0,
        models_loaded=mgr.models_loaded,
        default_narrator_embedding=narrator_path if os.path.exists(narrator_path) else None,
    )


@app.post("/internal/tts/create-voice", response_model=CreateVoiceResponse)
async def create_voice(req: CreateVoiceRequest):
    mgr = get_model_manager()
    data_dir = app.state.data_dir
    ffmpeg_bin = app.state.ffmpeg_bin
    voice_id = f"{req.work_id}_{req.character_id}_v{req.version}"

    async with gpu_lock:
        loop = asyncio.get_running_loop()

        def _sync_create():
            # 1. Generate Voice Design waveform
            raw_wav, sr = mgr.generate_voice_design(
                instruct=req.voice_instruct,
                reference_text=req.reference_sample_text,
                language=req.language,
                voice_id=voice_id,
            )

            # 2. Extract embedding and save .pt
            pt_path = os.path.join(
                data_dir, "voices", f"{req.work_id}_{req.character_id}_v{req.version}.pt"
            )
            os.makedirs(os.path.dirname(pt_path), exist_ok=True)
            prompt_bytes = mgr.create_voice_clone_prompt(
                "dummy",
                req.reference_sample_text,
                voice_id=voice_id,
                instruct=req.voice_instruct,
                language=req.language,
                allow_recreation=req.allow_voice_recreation,
            )
            temp_pt = f"{pt_path}.tmp"
            with open(temp_pt, "wb") as f:
                f.write(prompt_bytes)
            os.replace(temp_pt, pt_path)

            mgr.embedding_cache.put(pt_path, prompt_bytes)

            # 3. DSP normalization and preview MP3 encoding
            normalized_pcm = process_audio_pipeline(raw_wav, sr)
            # Keep complete conditioning above; cap only the separately encoded audition.
            normalized_pcm = normalized_pcm[:15 * 24000]
            preview_path = os.path.join(
                data_dir, "previews", f"{req.work_id}_{req.character_id}_v{req.version}.mp3"
            )
            os.makedirs(os.path.dirname(preview_path), exist_ok=True)
            encode_f32_to_mp3(
                audio=normalized_pcm,
                output_path=preview_path,
                ffmpeg_bin=ffmpeg_bin,
                write_xing=0,
                id3_version=0,
            )

            duration_ms = int(len(normalized_pcm) / 24000.0 * 1000)
            return pt_path, preview_path, duration_ms, hashlib.sha256(prompt_bytes).hexdigest()

        pt_path, preview_path, duration_ms, fingerprint = await loop.run_in_executor(gpu_executor, _sync_create)

    return CreateVoiceResponse(
        work_id=req.work_id,
        character_id=req.character_id,
        version=req.version,
        embedding_path=pt_path,
        preview_audio_path=preview_path,
        duration_ms=duration_ms,
        artifact_provider=mgr.provider_mode,
        artifact_fingerprint=fingerprint,
    )


@app.post("/internal/tts/synthesize-line", response_model=SynthesizeLineResponse)
async def synthesize_line(req: SynthesizeLineRequest):
    mgr = get_model_manager()
    data_dir = app.state.data_dir
    ffmpeg_bin = app.state.ffmpeg_bin

    # Validate embedding path exists
    if not os.path.exists(req.embedding_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"EMBEDDING_NOT_FOUND: {req.embedding_path}",
        )
    if req.expected_artifact_provider and req.expected_artifact_provider != mgr.provider_mode:
        raise HTTPException(status_code=409,detail='VOICE_PROVIDER_MISMATCH: Return to review to select a compatible voice.')
    if req.expected_artifact_fingerprint:
        with open(req.embedding_path,'rb') as stream:
            fingerprint=hashlib.sha256(stream.read()).hexdigest()
        if fingerprint != req.expected_artifact_fingerprint:
            raise HTTPException(status_code=409,detail='VOICE_ARTIFACT_CHANGED: Return to review to select a replacement voice.')

    clean_text = req.text.strip()
    if not clean_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="INVALID_PAYLOAD: text cannot be empty",
        )

    # Sanitize text
    clean_text = re.sub(r"\s+", " ", clean_text)

    async with gpu_lock:
        loop = asyncio.get_running_loop()

        def _sync_synthesize():
            # Check if embedding in cache
            prompt = mgr.embedding_cache.get(req.embedding_path)
            if prompt is None:
                with open(req.embedding_path, "rb") as f:
                    prompt = f.read()
                mgr.embedding_cache.put(req.embedding_path, prompt)

            # Synthesize
            raw_wav, sr = mgr.generate_voice_clone(
                text=clean_text,
                voice_clone_prompt=prompt,
                language=req.language,
            )

            # DSP Normalization
            normalized_pcm = process_audio_pipeline(raw_wav, sr)

            # Output path
            seg_dir = os.path.join(data_dir, "audio", req.chapter_id)
            os.makedirs(seg_dir, exist_ok=True)
            output_path = os.path.join(seg_dir, f"seg_{req.order:05d}_v{req.version}.mp3")

            encode_f32_to_mp3(
                audio=normalized_pcm,
                output_path=output_path,
                ffmpeg_bin=ffmpeg_bin,
                write_xing=0,
                id3_version=0,
            )

            duration_ms = int(len(normalized_pcm) / 24000.0 * 1000)
            return output_path, duration_ms

        audio_path, duration_ms = await loop.run_in_executor(gpu_executor, _sync_synthesize)

    return SynthesizeLineResponse(
        segment_id=req.segment_id,
        chapter_id=req.chapter_id,
        order=req.order,
        version=req.version,
        audio_path=audio_path,
        duration_ms=duration_ms,
        sample_rate=24000,
        provider_mode=mgr.provider_mode,
    )


@app.post("/internal/tts/stitch-chapter", response_model=StitchChapterResponse)
async def stitch_chapter(req: StitchChapterRequest):
    data_dir = app.state.data_dir
    ffmpeg_bin = app.state.ffmpeg_bin

    try:
        master_path, file_size_bytes, duration_ms = stitch_chapter_audio(
            work_id=req.work_id,
            chapter_id=req.chapter_id,
            segments=req.segments,
            metadata=req.metadata,
            data_dir=data_dir,
            ffmpeg_bin=ffmpeg_bin,
        )
    except FileNotFoundError as fnf:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"INVALID_PAYLOAD: {fnf}",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"TTS_INFERENCE_ERROR: {exc}",
        )

    return StitchChapterResponse(
        work_id=req.work_id,
        chapter_id=req.chapter_id,
        master_audio_path=master_path,
        file_size_bytes=file_size_bytes,
        total_duration_ms=duration_ms,
    )
