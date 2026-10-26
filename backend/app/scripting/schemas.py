"""Strict Pydantic v2 schemas for LLM Script Deconstruction and Voice Architecture."""

from __future__ import annotations

import re
from typing import List, Literal, Sequence
from pydantic import BaseModel, ConfigDict, Field, field_validator


class RegistryCharacterContext(BaseModel):
    """Minimal character state injected into the LLM prompt."""
    model_config = ConfigDict(extra="forbid")

    character_id: str
    canonical_name: str
    aliases: List[str] = Field(default_factory=list)
    gender: Literal["male", "female", "neutral"]


class PreviousSegmentContext(BaseModel):
    """Read-only trailing segment from Chunk N-1 for cross-chunk coreference."""
    model_config = ConfigDict(extra="forbid")

    speaker_id: str
    text: str


class ChunkParagraphInput(BaseModel):
    """Input paragraph from Spec 01 text normalizer."""
    model_config = ConfigDict(extra="forbid")

    paragraph_index: int
    text: str


class NewCharacterSpec(BaseModel):
    """Newly discovered speaking character emitted by the LLM."""
    model_config = ConfigDict(extra="forbid")

    character_id: str = Field(..., pattern=r"^char_[a-z0-9_]+$")
    canonical_name: str
    aliases: List[str]
    gender: Literal["male", "female", "neutral"]
    voice_instruct: str
    reference_sample_text: str

    @field_validator("character_id", mode="before")
    @classmethod
    def normalize_char_id(cls, v: str) -> str:
        clean = re.sub(r"[^a-z0-9_]", "_", str(v).lower().strip())
        clean = re.sub(r"_+", "_", clean).strip("_")
        if not clean.startswith("char_"):
            clean = f"char_{clean}"
        return clean


class CharacterUpdateSpec(BaseModel):
    """Alias or canonical name update for an already-registered character."""
    model_config = ConfigDict(extra="forbid")

    character_id: str = Field(..., pattern=r"^char_[a-z0-9_]+$")
    canonical_name: str
    new_aliases: List[str]

    @field_validator("character_id", mode="before")
    @classmethod
    def normalize_char_id(cls, v: str) -> str:
        clean = re.sub(r"[^a-z0-9_]", "_", str(v).lower().strip())
        clean = re.sub(r"_+", "_", clean).strip("_")
        if not clean.startswith("char_"):
            clean = f"char_{clean}"
        return clean


class ScriptSegmentSpec(BaseModel):
    """Atomic chronological segment for TTS synthesis."""
    model_config = ConfigDict(extra="forbid")

    segment_order: int
    paragraph_index: int
    speaker_id: str
    text: str
    is_paragraph_end: bool


class ChunkAnalysisResult(BaseModel):
    """Top-level structured output enforced via vLLM/OpenAI Guided Decoding."""
    model_config = ConfigDict(extra="forbid")

    new_characters: List[NewCharacterSpec]
    character_updates: List[CharacterUpdateSpec]
    segments: List[ScriptSegmentSpec]

