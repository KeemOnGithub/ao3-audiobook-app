"""Pydantic v2 schemas for AO3 Ingestion & Text Normalization."""

from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class NormalizedParagraph(BaseModel):
    """An indexed, sanitized narrative paragraph."""
    model_config = ConfigDict(frozen=True)

    paragraph_index: int = Field(
        ...,
        description="0-based sequential paragraph index across the chapter"
    )
    text: str = Field(
        ...,
        description="Sanitized UTF-8 text with *italic* and **bold** markers"
    )
    word_count: int = Field(
        ...,
        description="Whitespace-delimited word count"
    )
    scene_break_after: bool = Field(
        default=False,
        description="True if a [SCENE_BREAK] immediately follows this paragraph"
    )


class NarrativeChunk(BaseModel):
    """A bounded collection of paragraphs prepared for sequential LLM script deconstruction."""
    chunk_index: int = Field(
        ...,
        description="0-based chunk index (0 = Fast Start)"
    )
    is_fast_start: bool = Field(
        ...,
        description="True when chunk_index == 0"
    )
    is_last_chunk: bool = Field(
        ...,
        description="True for the final chunk of the chapter"
    )
    word_count: int = Field(
        ...,
        description="Total words in paragraphs (excluding read_only_prior_context)"
    )
    start_paragraph_index: int = Field(
        ...,
        description="0-based index of first paragraph in this chunk"
    )
    end_paragraph_index: int = Field(
        ...,
        description="0-based index of last paragraph in this chunk"
    )
    read_only_prior_context: List[NormalizedParagraph] = Field(
        default_factory=list,
        description="Last 2 paragraphs from prior chunk for pronoun and dialogue continuity"
    )
    paragraphs: List[NormalizedParagraph] = Field(
        ...,
        description="The active paragraphs to be analyzed and synthesized in this chunk"
    )
    formatted_text: str = Field(
        ...,
        description="Paragraphs joined by '\\n\\n' (and '\\n\\n[SCENE_BREAK]\\n\\n' where scene_break_after is True)"
    )


class ChapterMetadata(BaseModel):
    """Rich story and chapter metadata extracted from AO3 DOM."""
    work_id: str
    chapter_id: str
    canonical_url: str
    title: str
    author: str
    authors: List[str]
    fandoms: List[str]
    canonical_characters: List[str]
    relationship_tags: List[str] = Field(default_factory=list)
    language: str = "English"
    chapter_index: int = 1
    total_chapters: Optional[int] = None
    chapter_title: str
    next_chapter_url: Optional[str] = None
    prev_chapter_url: Optional[str] = None
    total_words: int
    total_paragraphs: int
    total_chunks: int


class IngestionResult(BaseModel):
    """Complete output of Phase 1 ingestion pipeline."""
    metadata: ChapterMetadata
    chunks: List[NarrativeChunk]

