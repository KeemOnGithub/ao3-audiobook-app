"""Pydantic v2 schemas for TTS Microservice API and Audio Pipeline per Spec 03."""

from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class CreateVoiceRequest(BaseModel):
    allow_voice_recreation: bool = True
    work_id: str = Field(..., pattern=r"^[a-zA-Z0-9_-]+$")
    character_id: str = Field(..., pattern=r"^[a-zA-Z0-9_-]+$")
    version: int = Field(default=1, ge=1)
    voice_instruct: str = Field(..., min_length=10, max_length=600)
    reference_sample_text: str = Field(
        default="I never thought we would end up here, standing at the edge of everything we knew.",
        min_length=20,
        max_length=250,
    )
    language: str = Field(default="English")


class CreateVoiceResponse(BaseModel):
    artifact_provider: Optional[str] = None
    artifact_fingerprint: Optional[str] = None
    work_id: str
    character_id: str
    version: int
    embedding_path: str
    preview_audio_path: str
    duration_ms: int


class SynthesizeLineRequest(BaseModel):
    expected_artifact_provider: Optional[str] = None
    expected_artifact_fingerprint: Optional[str] = None
    chapter_id: str = Field(..., pattern=r"^[a-zA-Z0-9_-]+$")
    segment_id: str = Field(..., pattern=r"^[a-zA-Z0-9_-]+$")
    order: int = Field(..., ge=0)
    version: int = Field(default=1, ge=1)
    text: str = Field(..., min_length=1, max_length=2000)
    embedding_path: str
    language: str = Field(default="English")


class SynthesizeLineResponse(BaseModel):
    segment_id: str
    chapter_id: str
    order: int
    version: int
    audio_path: str
    duration_ms: int
    sample_rate: int = 24000
    provider_mode: str = "simulation"


class StitchSegmentItem(BaseModel):
    audio_path: str
    pause_after: Literal["intra_paragraph", "paragraph_end", "scene_break", "none"]


class StitchChapterMetadata(BaseModel):
    title: str          # TIT2: Chapter Title
    author: str         # TPE1: AO3 Author
    work_name: str      # TALB: Fanfiction Work Title
    chapter_number: int # TRCK: Chapter Index (1-based)


class StitchChapterRequest(BaseModel):
    work_id: str
    chapter_id: str
    segments: List[StitchSegmentItem] = Field(..., min_length=1)
    metadata: StitchChapterMetadata


class StitchChapterResponse(BaseModel):
    work_id: str
    chapter_id: str
    master_audio_path: str
    file_size_bytes: int
    total_duration_ms: int


class TTSCapabilities(BaseModel):
    voice_create: bool = True
    speak: bool = True
    stitch: bool = True


class TTSHealthResponse(BaseModel):
    status: str
    provider_mode: str
    provider_base_url: Optional[str] = None
    capabilities: TTSCapabilities = Field(default_factory=TTSCapabilities)
    vram_allocated_mb: float = 0.0
    models_loaded: List[str] = Field(default_factory=list)
    default_narrator_embedding: Optional[str] = None

