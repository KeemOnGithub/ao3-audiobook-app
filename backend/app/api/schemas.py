"""Strict Pydantic request and response schemas for FastAPI Backend per Spec 05."""

from enum import Enum
from typing import List, Optional, Union
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChapterStatus(str, Enum):
    PENDING = "PENDING"
    STREAMING = "STREAMING"
    MASTERING = "MASTERING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class SegmentStatus(str, Enum):
    QUEUED = "QUEUED"
    SYNTHESIZING = "SYNTHESIZING"
    READY = "READY"
    FAILED = "FAILED"


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class LibraryChapterSchema(StrictBaseModel):
    review_phase: str = 'ANALYZING'
    chapter_uuid: str
    chapter_id: str
    chapter_number: int
    chapter_title: str
    source_url: str
    status: ChapterStatus
    master_audio_available: bool
    created_at: str
    updated_at: str


class LibraryWorkSchema(StrictBaseModel):
    work_id: str
    title: str
    author: str
    created_at: str
    updated_at: str
    chapters: List[LibraryChapterSchema] = Field(default_factory=list)


class LibraryResponse(StrictBaseModel):
    works: List[LibraryWorkSchema] = Field(default_factory=list)


class ChapterLoadRequest(StrictBaseModel):
    ao3_url: Optional[str] = Field(
        default=None,
        description="Canonical AO3 work or chapter URL."
    )
    url: Optional[str] = Field(
        default=None,
        description="Alias for ao3_url."
    )
    force_regenerate: Union[bool, str] = Field(
        default=False,
        description="URL loads always start fresh, including voices and profiles. AUDIO_ONLY explicitly preserves the current script and voices."
    )

    @model_validator(mode="after")
    def resolve_url_and_regen(self):
        if not self.ao3_url and self.url:
            self.ao3_url = self.url
        if not self.ao3_url:
            raise ValueError("Either 'ao3_url' or 'url' must be provided.")
        if isinstance(self.force_regenerate, str):
            val = self.force_regenerate.upper()
            if val in ("AUDIO_ONLY",):
                self.force_regenerate = "AUDIO_ONLY"
            elif val in ("TRUE", "FULL", "1"):
                self.force_regenerate = "FULL"
            else:
                self.force_regenerate = False
        elif self.force_regenerate is True:
            self.force_regenerate = "FULL"
        return self


class CharacterVoiceUpdateRequest(StrictBaseModel):
    voice_instruct: str = Field(
        ...,
        min_length=10,
        max_length=600,
        description="Natural language acoustic prompt describing character voice."
    )
    current_playback_order: Optional[int] = Field(
        default=None,
        alias="playback_order",
        description="Current segment_order playing in frontend to prioritize subsequent lines."
    )
    current_chapter_uuid: Optional[str] = Field(
        default=None,
        alias="chapter_id",
        description="Active chapter UUID to prioritize."
    )

    @model_validator(mode="after")
    def resolve_defaults(self):
        if self.current_playback_order is None:
            self.current_playback_order = 0
        if not self.current_chapter_uuid:
            raise ValueError("current_chapter_uuid or chapter_id must be provided.")
        return self


class PlaybackSeekRequest(StrictBaseModel):
    current_playback_order: Optional[int] = Field(
        default=None,
        alias="target_segment_order",
        description="Target segment_order the user jumped to."
    )

    @model_validator(mode="after")
    def resolve_target(self):
        if self.current_playback_order is None:
            self.current_playback_order = 0
        return self


class ChapterLoadResponse(StrictBaseModel):
    chapter_uuid: str
    work_id: str
    chapter_id: str
    title: str
    work_title: Optional[str] = None
    chapter_title: Optional[str] = None
    chapter_number: int = 1
    total_chapters: int = 1
    author: str = "AO3 Author"
    status: ChapterStatus
    total_chunks: int = 1
    run_generation: int = 0
    state_revision: int = 0


class CharacterSchema(StrictBaseModel):
    reference_text: str = ''
    artifact_provider: Optional[str] = None
    artifact_fingerprint: Optional[str] = None
    character_uuid: str
    canonical_name: str
    voice_instruct: str
    voice_version: int
    preview_url: Optional[str] = None
    voice_status: str = "PENDING"
    aliases: List[str] = Field(default_factory=list)
    is_narrator: bool = False
    segment_count: int = 0


class SegmentSchema(StrictBaseModel):
    segment_uuid: str
    segment_order: int
    chunk_index: int = 0
    paragraph_index: Optional[int] = None
    source_continuation: bool = False
    character_uuid: str
    speaker_name: str = "Narrator"
    is_narrator: bool = False
    is_dialogue: bool = False
    text: str
    status: SegmentStatus
    error_message: Optional[str] = None
    retry_count: int = 0
    audio_url: Optional[str] = None
    duration_ms: Optional[int] = None
    is_paragraph_end: bool = False
    voice_version: int = 1


class ChapterStateResponse(StrictBaseModel):
    review_phase: str = 'ANALYZING'
    review_revision: int = 0
    analysis_completed_at: Optional[str] = None
    approved_run_generation: Optional[int] = None
    approved_review_revision: Optional[int] = None
    approved_at: Optional[str] = None
    narration_authorized: bool = False
    can_approve: bool = False
    approval_blockers: List[dict] = Field(default_factory=list)
    voice_preview_candidates: List[dict] = Field(default_factory=list)
    chapter_uuid: str
    work_id: Optional[str] = None
    work_title: Optional[str] = None
    chapter_title: Optional[str] = None
    chapter_number: Optional[int] = None
    total_chapters: int = 1
    author: Optional[str] = None
    source_url: Optional[str] = None
    status: ChapterStatus
    error_message: Optional[str] = None
    run_generation: int = 0
    state_revision: int = 0
    total_chunks: int = 0
    scripted_chunks: int = 0
    master_mp3_ready: bool = False
    master_mp3_url: Optional[str] = None
    master_mp3_progress: int = 0
    total_segments: int = 0
    ready_segments: int = 0
    characters: List[CharacterSchema] = Field(default_factory=list)
    segments: List[SegmentSchema] = Field(default_factory=list)


class RetryFailedRequest(StrictBaseModel):
    segment_uuids: Optional[List[str]] = Field(
        default=None,
        description="Optional list of specific failed segment UUIDs to retry. If omitted or empty, retries entire failed chapter.",
    )



class VoiceUpdateResponse(StrictBaseModel):
    character_uuid: str
    voice_version: int
    preview_url: str
    affected_segments_count: int


class MasterDownloadProgressResponse(StrictBaseModel):
    status: str
    progress_pct: float
    total_segments: int
    processed_segments: int
    estimated_remaining_seconds: Optional[int]


class ReviewIdentityRequest(BaseModel):
    model_config = ConfigDict(extra='forbid',str_strip_whitespace=True)
    expected_run_generation: int = Field(ge=0)
    expected_review_revision: int = Field(ge=0)


class VoicePreviewRequest(ReviewIdentityRequest):
    voice_instruct: str = Field(min_length=10,max_length=600)
    reference_text: Optional[str] = Field(default=None,min_length=20,max_length=250)


class RenameReviewCharacterRequest(ReviewIdentityRequest):
    canonical_name: str = Field(min_length=1,max_length=120)


class AddReviewCharacterRequest(RenameReviewCharacterRequest):
    voice_instruct: str = Field(min_length=10,max_length=600)


class MergeReviewCharactersRequest(ReviewIdentityRequest):
    source_character_uuid: str = Field(min_length=1,max_length=120)
    target_character_uuid: str = Field(min_length=1,max_length=120)


class ReassignReviewSpeakersRequest(ReviewIdentityRequest):
    segment_uuids: List[str] = Field(min_length=1,max_length=1000)
    target_character_uuid: str = Field(min_length=1,max_length=120)
