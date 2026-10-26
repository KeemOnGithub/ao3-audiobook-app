"""Typed domain models and DTOs for SQLite persistence per Spec 04."""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class WorkDTO:
    work_id: str
    title: str
    author: str
    fandoms: List[str]
    created_at: str = ""
    updated_at: str = ""


@dataclass
class LibraryChapterDTO:
    work_id: str
    title: str
    author: str
    work_created_at: str
    work_updated_at: str
    chapter_uuid: str
    chapter_id: str
    chapter_number: int
    chapter_title: str
    source_url: str
    status: str
    master_mp3_path: Optional[str]
    created_at: str
    updated_at: str
    review_phase: str = 'ANALYZING'
    narration_authorized: bool = False


@dataclass
class ChapterDTO:
    chapter_uuid: str
    work_id: str
    chapter_id: str
    chapter_number: int
    chapter_title: str
    source_url: str
    status: str = "PENDING"
    run_generation: int = 0
    state_revision: int = 0
    total_chunks: int = 0
    llm_completed_chunks: int = 0
    total_segments: int = 0
    completed_segments: int = 0
    master_mp3_path: Optional[str] = None
    master_version: int = 0
    error_message: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    voice_scope: str = ""


    review_phase: str = "ANALYZING"
    review_revision: int = 0
    analysis_completed_at: Optional[str] = None
    approved_run_generation: Optional[int] = None
    approved_review_revision: Optional[int] = None
    approved_at: Optional[str] = None

    @property
    def approval_valid(self) -> bool:
        return (self.analysis_completed_at is not None
                and self.approved_at is not None
                and self.approved_run_generation == self.run_generation
                and self.approved_review_revision == self.review_revision)

    @property
    def narration_authorized(self) -> bool:
        return self.review_phase == "SYNTHESIZING" and self.approval_valid


@dataclass
class CharacterDTO:
    character_uuid: str
    work_id: str
    character_key: str
    canonical_name: str
    is_narrator: int = 0
    aliases: List[str] = field(default_factory=list)
    voice_instruct: str = ""
    reference_text: str = ""
    voice_status: str = "PENDING"
    embedding_pt_path: Optional[str] = None
    preview_mp3_path: Optional[str] = None
    voice_version: int = 1
    artifact_provider: Optional[str] = None
    artifact_fingerprint: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    voice_scope: str = ""


@dataclass
class SegmentDTO:
    segment_uuid: str
    chapter_uuid: str
    chunk_index: int
    segment_order: int
    character_uuid: str
    text_content: str
    paragraph_index: Optional[int] = None
    source_continuation: int = 0
    delivery_instruct: Optional[str] = None
    is_paragraph_end: int = 0
    audio_path: Optional[str] = None
    duration_ms: Optional[int] = None
    status: str = "QUEUED"
    voice_version_used: int = 1
    retry_count: int = 0
    claimed_by: Optional[str] = None
    claimed_generation: Optional[int] = None
    claimed_at: Optional[str] = None
    last_error_code: Optional[str] = None
    error_message: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class SegmentClaim:
    segment_uuid: str
    run_generation: int
    target_voice_version: int
    worker_token: str
    claimed_at: str
    character_uuid: str = ""
    approved_review_revision: int = -1


@dataclass
class MasterClaim:
    chapter_uuid: str
    run_generation: int
    master_version: int
    approved_review_revision: int = -1


@dataclass
class SegmentResult:
    segment_uuid: str
    status: str
    retry_count: int
    retryable: bool
    error_code: Optional[str] = None
    error_message: Optional[str] = None

