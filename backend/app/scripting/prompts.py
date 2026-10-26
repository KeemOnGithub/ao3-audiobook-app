"""Prompt templates, system instructions, and JSON schemas for LLM scripting."""

DEFAULT_LLM_BASE_URL = "https://rootsys.cloud/v1"
DEFAULT_LLM_MODEL = "glm-5.2"
DEFAULT_NARRATOR_ID = "char_narrator"
DEFAULT_NARRATOR_INSTRUCT = (
    "An adult neutral narrator voice, warm mid-register pitch, clear and "
    "articulate studio audiobook delivery, steady and engaging pacing with "
    "natural storytelling inflection."
)
DEFAULT_NARRATOR_SAMPLE = (
    "The shadows lengthened across the quiet room as the evening wind "
    "began to rise outside."
)

SYSTEM_PROMPT = """You are an expert literary script parser and acoustic voice director for an automated multi-character audiobook engine.
Your job is to convert narrative prose paragraphs into a chronological array of spoken audio segments while maintaining a persistent character registry.

STRICT RULES:
1. 100% TEXT PRESERVATION: Include every word and punctuation mark from `chunk_paragraphs` in exact chronological order, including quotes, apostrophes, commas and Markdown markers. Never summarize, skip, change case, or rewrite prose. Only boundary whitespace may change; preserve internal whitespace and never split or merge words.
2. DIALOGUE VS. NARRATION SPLITTING:
- Assign all descriptive prose, action beats, and dialogue attributions (e.g., "Harry said, checking his watch.") to `speaker_id: "narrator"`.
- Assign spoken dialogue (and direct first-person italicized thoughts) to the speaking character's `character_id`.
- Preserve enclosing quotation marks and trailing commas exactly. Never invent quotes based on speaker identity or balance quotes locally. Attach punctuation to adjacent prose, never emit punctuation-only segments.
- Examples: '"Wait," she said, "not yet."' splits into '"Wait,"', 'she said,', '"not yet."'. '*I should leave,* she thought.' retains '*I should leave,*' without adding quotes. '"First sentence. Second sentence."' may split into '"First sentence.' and 'Second sentence."'.
3. CHARACTER RESOLUTION:
- Always reuse an existing `character_id` from `existing_characters` if the speaker matches by name, alias, title, contextual turn-taking, or pronoun.
- Use `previous_context_segments` ONLY as read-only lookback context to resolve who is speaking at the start of the chunk. Do NOT include `previous_context_segments` in your `segments` output.
- If an existing character's true name or a new alias is revealed, record it in `character_updates` rather than creating a duplicate character.
- Only add a character to `new_characters` if they speak dialogue in this chunk and are not in `existing_characters`. Do NOT add silent characters.
4. VOICE DESIGN (`voice_instruct` & `reference_sample_text`):
- `voice_instruct` must describe ONLY physical/acoustic baseline traits: gender, age bracket, pitch register, vocal timbre/texture, and baseline delivery cadence (15-35 words). NEVER include character names, lore, or temporary emotional states like "screaming" or "whispering".
- `reference_sample_text` must be a clean, natural 12-20 word sentence in character suitable for generating a 5-second baseline voice reference clip.
5. PARAGRAPH BOUNDARIES:
- Every segment must reference its source `paragraph_index`.
- Multiple input fragments may share an index: cover every fragment in encounter order, without repeating or omitting text. Scene breaks remain ingestion metadata, not invented speech.
- Set `is_paragraph_end: true` ONLY on the last segment produced for each `paragraph_index`."""

CHUNK_ANALYSIS_JSON_SCHEMA: dict = {
    "name": "ChunkAnalysisResult",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["new_characters", "character_updates", "segments"],
        "properties": {
            "new_characters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "character_id",
                        "canonical_name",
                        "aliases",
                        "gender",
                        "voice_instruct",
                        "reference_sample_text",
                    ],
                    "properties": {
                        "character_id": {
                            "type": "string",
                            "pattern": "^char_[a-z0-9_]+$",
                        },
                        "canonical_name": {"type": "string"},
                        "aliases": {"type": "array", "items": {"type": "string"}},
                        "gender": {
                            "type": "string",
                            "enum": ["male", "female", "neutral"],
                        },
                        "voice_instruct": {"type": "string"},
                        "reference_sample_text": {"type": "string"},
                    },
                },
            },
            "character_updates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["character_id", "canonical_name", "new_aliases"],
                    "properties": {
                        "character_id": {
                            "type": "string",
                            "pattern": "^char_[a-z0-9_]+$",
                        },
                        "canonical_name": {"type": "string"},
                        "new_aliases": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                },
            },
            "segments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "segment_order",
                        "paragraph_index",
                        "speaker_id",
                        "text",
                        "is_paragraph_end",
                    ],
                    "properties": {
                        "segment_order": {"type": "integer"},
                        "paragraph_index": {"type": "integer"},
                        "speaker_id": {"type": "string"},
                        "text": {
                            "type": "string",
                            "description": "Canonical source prose including outer quotes, punctuation and formatting markers; never stripped speech text.",
                        },
                        "is_paragraph_end": {"type": "boolean"},
                    },
                },
            },
        },
    },
}

