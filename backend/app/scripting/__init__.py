"""LLM Script Deconstruction and Character Voice Architecture package."""

from backend.app.scripting.analyzer import LLMScriptAnalyzer
from backend.app.scripting.prompts import (
    CHUNK_ANALYSIS_JSON_SCHEMA,
    DEFAULT_LLM_BASE_URL,
    DEFAULT_LLM_MODEL,
    DEFAULT_NARRATOR_ID,
    DEFAULT_NARRATOR_INSTRUCT,
    DEFAULT_NARRATOR_SAMPLE,
    SYSTEM_PROMPT,
)
from backend.app.scripting.schemas import (
    CharacterUpdateSpec,
    ChunkAnalysisResult,
    ChunkParagraphInput,
    NewCharacterSpec,
    PreviousSegmentContext,
    RegistryCharacterContext,
    ScriptSegmentSpec,
)

__all__ = [
    "LLMScriptAnalyzer",
    "RegistryCharacterContext",
    "PreviousSegmentContext",
    "ChunkParagraphInput",
    "NewCharacterSpec",
    "CharacterUpdateSpec",
    "ScriptSegmentSpec",
    "ChunkAnalysisResult",
    "CHUNK_ANALYSIS_JSON_SCHEMA",
    "SYSTEM_PROMPT",
    "DEFAULT_LLM_BASE_URL",
    "DEFAULT_LLM_MODEL",
    "DEFAULT_NARRATOR_ID",
    "DEFAULT_NARRATOR_INSTRUCT",
    "DEFAULT_NARRATOR_SAMPLE",
]

