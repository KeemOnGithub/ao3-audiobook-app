"""LLM Script Analyzer implementation for OpenAI-compatible text model endpoints per Spec 02."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Optional, Sequence

import httpx

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

logger = logging.getLogger(__name__)


class LLMOutputTruncated(Exception):
    """Raised when the LLM response is truncated by output token limits (finish_reason == 'length')."""
    def __init__(self, message: str, chunk_index: int = 0, finish_reason: str = "length", detail: Optional[dict] = None):
        super().__init__(message)
        self.chunk_index = chunk_index
        self.finish_reason = finish_reason
        self.detail = detail or {}


class LLMScriptAnalyzer:
    """Executes structured JSON script analysis on prose chunks using OpenAI-compatible models."""

    def __init__(
        self,
        base_url: str = DEFAULT_LLM_BASE_URL,
        api_key: str = "EMPTY",
        model: str = DEFAULT_LLM_MODEL,
        max_retries: int = 2,
        client: Optional[httpx.AsyncClient] = None,
        max_tokens: Optional[int] = None,
    ) -> None:
        url = base_url.rstrip("/")
        if not url.endswith("/v1"):
            url = f"{url}/v1"
        self.base_url = url
        self.api_key = api_key
        self.model = model
        self.max_retries = max_retries
        self._external_client = client
        if max_tokens is not None:
            configured_max_tokens = max_tokens
        else:
            configured_max_tokens = int(os.getenv("LLM_MAX_TOKENS", "8192"))
        if configured_max_tokens < 1:
            raise ValueError("LLM max_tokens must be greater than zero")
        self.max_tokens = configured_max_tokens
        
        # Timeout disabled by default (or set via LLM_TIMEOUT env var in seconds)
        env_timeout = os.getenv("LLM_TIMEOUT")
        if env_timeout is not None:
            val = float(env_timeout)
            self.timeout_sec: Optional[float] = None if val <= 0 else val
        else:
            self.timeout_sec = None

    async def analyze_chunk(
        self,
        chunk_index: int,
        paragraphs: Sequence[ChunkParagraphInput],
        existing_characters: Sequence[RegistryCharacterContext],
        previous_context_segments: Optional[Sequence[PreviousSegmentContext]] = None,
    ) -> ChunkAnalysisResult:
        """Analyze a prose chunk with bounded validation retries, failing closed."""
        if not paragraphs:
            return ChunkAnalysisResult(
                new_characters=[], character_updates=[], segments=[]
            )

        timeout_sec = self.timeout_sec
        user_payload = {
            "existing_characters": [c.model_dump() for c in existing_characters],
            "previous_context_segments": [
                s.model_dump() for s in (previous_context_segments or [])[-3:]
            ],
            "chunk_paragraphs": [p.model_dump() for p in paragraphs],
        }

        for attempt in range(self.max_retries + 1):
            temperature = 0.1 if attempt == 0 else 0.2
            start_time = time.perf_counter()
            try:
                logger.info(
                    "Starting Chunk %d LLM deconstruction (%d paragraphs, attempt %d/%d, timeout=%s)...",
                    chunk_index,
                    len(paragraphs),
                    attempt + 1,
                    self.max_retries + 1,
                    f"{timeout_sec}s" if timeout_sec is not None else "DISABLED",
                )
                raw_result = await self._call_qwen_flash_next(
                    chunk_index=chunk_index,
                    user_payload=user_payload,
                    temperature=temperature,
                    timeout_sec=timeout_sec,
                )
                reconciled = self._reconcile_and_validate(
                    result=raw_result,
                    paragraphs=paragraphs,
                    existing_characters=existing_characters,
                )
                elapsed = time.perf_counter() - start_time
                logger.info(
                    "Chunk %d LLM deconstruction FINISHED in %.2fs (found %d new characters, %d segments).",
                    chunk_index,
                    elapsed,
                    len(reconciled.new_characters),
                    len(reconciled.segments),
                )
                return reconciled
            except LLMOutputTruncated:
                raise
            except Exception as exc:
                elapsed = time.perf_counter() - start_time
                logger.warning(
                    "Chunk %d LLM analysis attempt %d/%d failed after %.2fs: %s",
                    chunk_index,
                    attempt + 1,
                    self.max_retries + 1,
                    elapsed,
                    exc,
                )

        logger.error(
            "All LLM attempts exhausted for chunk %d. Failing closed per Spec 07.",
            chunk_index,
        )
        raise RuntimeError(f"LLM_ANALYSIS_FAILED: All {self.max_retries + 1} analysis attempts exhausted for chunk {chunk_index}.")

    async def _call_qwen_flash_next(
        self,
        chunk_index: int,
        user_payload: dict,
        temperature: float,
        timeout_sec: Optional[float] = None,
    ) -> ChunkAnalysisResult:
        """Calls OpenAI-compatible vLLM/SGLang endpoint with Qwen3.8-Flash-Next."""
        request_body = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT + "\n\nReturn only a JSON object matching this schema. "
                    "Do not echo input fields or add character metadata to segments.\n"
                    + json.dumps(CHUNK_ANALYSIS_JSON_SCHEMA["schema"]),
                },
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False),
                },
            ],
            "temperature": temperature,
            "top_p": 0.9,
            "presence_penalty": 0.0,
            "frequency_penalty": 0.0,
            "max_tokens": self.max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": CHUNK_ANALYSIS_JSON_SCHEMA,
            },
        }

        # Disable Qwen thinking mode if applicable
        if "qwen" in self.model.lower():
            request_body["chat_template_kwargs"] = {"enable_thinking": False}

        headers = {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
            "ngrok-skip-browser-warning": "1",
        }

        if self._external_client:
            response = await self._external_client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=request_body,
                timeout=timeout_sec,
            )
            response.raise_for_status()
            data = response.json()
        else:
            async with httpx.AsyncClient(timeout=timeout_sec) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=request_body,
                )
                response.raise_for_status()
                data = response.json()

        choice = data["choices"][0]
        finish_reason = choice.get("finish_reason")
        if finish_reason == "length":
            raise LLMOutputTruncated(
                f"LLM output truncated for chunk {chunk_index} at max_tokens={self.max_tokens} "
                "(finish_reason='length'). Increase LLM_MAX_TOKENS or reduce the chunk size.",
                chunk_index=chunk_index,
                finish_reason="length",
                detail={"usage": data.get("usage"), "model": self.model},
            )

        content: str = choice["message"]["content"]
        # Strip residual <think>...</think> blocks if the server ignored chat_template_kwargs or for reasoning models
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        # Strip markdown code fences if present (e.g. ```json ... ```)
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.IGNORECASE)
            content = re.sub(r"\s*```$", "", content)
            content = content.strip()
        # Some OpenAI-compatible models follow the requested schema semantically
        # but emit the common `segment_index` name instead of `segment_order`.
        # Normalize that harmless alias before strict validation.
        parsed = json.loads(content)
        for index, segment in enumerate(parsed.get("segments", [])):
            # Some providers emit null character metadata on segment objects.
            # Discard only empty placeholders, not unexpected non-null data.
            for field in ("character_id", "voice_instruct", "reference_sample_text"):
                if field in segment and segment[field] is None:
                    del segment[field]
            if "segment_order" not in segment and "segment_index" in segment:
                segment["segment_order"] = segment.pop("segment_index")
            if "segment_order" not in segment:
                segment["segment_order"] = index
        return ChunkAnalysisResult.model_validate(parsed)

    def _reconcile_and_validate(
        self,
        result: ChunkAnalysisResult,
        paragraphs: Sequence[ChunkParagraphInput],
        existing_characters: Sequence[RegistryCharacterContext],
    ) -> ChunkAnalysisResult:
        """Enforces referential integrity, deduplication, prose fidelity, and paragraph boundaries."""
        existing_by_id = {c.character_id: c for c in existing_characters}
        existing_by_id.setdefault(
            DEFAULT_NARRATOR_ID,
            RegistryCharacterContext(
                character_id=DEFAULT_NARRATOR_ID,
                canonical_name="Narrator",
                aliases=[],
                gender="neutral",
            ),
        )

        # Map lowercase names/aliases -> existing character_id for deduplication
        name_to_existing_id: dict[str, str] = {}
        for char in existing_characters:
            name_to_existing_id[char.canonical_name.lower().strip()] = char.character_id
            for alias in char.aliases:
                name_to_existing_id[alias.lower().strip()] = char.character_id

        deduped_new_chars: list[NewCharacterSpec] = []
        updates: list[CharacterUpdateSpec] = list(result.character_updates)
        id_remap: dict[str, str] = {}

        for new_char in result.new_characters:
            matched_existing_id = (new_char.character_id in existing_by_id and new_char.character_id)
            if not matched_existing_id:
                matched_existing_id = name_to_existing_id.get(
                    new_char.canonical_name.lower().strip()
                )
            if not matched_existing_id:
                for alias in new_char.aliases:
                    if alias.lower().strip() in name_to_existing_id:
                        matched_existing_id = name_to_existing_id[alias.lower().strip()]
                        break

            if matched_existing_id:
                id_remap[new_char.character_id] = matched_existing_id
                updates.append(
                    CharacterUpdateSpec(
                        character_id=matched_existing_id,
                        canonical_name=existing_by_id[matched_existing_id].canonical_name,
                        new_aliases=list(
                            {new_char.canonical_name, *new_char.aliases}
                        ),
                    )
                )
            else:
                deduped_new_chars.append(new_char)
                existing_by_id[new_char.character_id] = RegistryCharacterContext(
                    character_id=new_char.character_id,
                    canonical_name=new_char.canonical_name,
                    aliases=new_char.aliases,
                    gender=new_char.gender,
                )
                name_to_existing_id[new_char.canonical_name.lower().strip()] = new_char.character_id
                for alias in new_char.aliases:
                    name_to_existing_id[alias.lower().strip()] = new_char.character_id

        # Clean segments and resolve orphan speaker_ids
        cleaned_segments: list[ScriptSegmentSpec] = []
        for seg in result.segments:
            cleaned_text = seg.text.strip()
            if not cleaned_text:
                continue
            if not any(character.isalnum() for character in cleaned_text):
                raise ValueError(
                    f"Prose fidelity: punctuation-only segment in paragraph {seg.paragraph_index}; retry required."
                )

            raw_speaker = seg.speaker_id.strip()
            if raw_speaker.lower() in ("narrator", "char_narrator"):
                raw_speaker = DEFAULT_NARRATOR_ID

            speaker_id = id_remap.get(raw_speaker, raw_speaker)
            if speaker_id not in existing_by_id:
                # Lookup by name/alias or synthesize safe fallback character
                resolved_id = name_to_existing_id.get(speaker_id.lower().strip())
                if resolved_id:
                    speaker_id = resolved_id
                else:
                    fallback_id = re.sub(r"[^a-z0-9_]", "_", speaker_id.lower())
                    if not fallback_id.startswith("char_"):
                        fallback_id = f"char_{fallback_id}"
                    if fallback_id not in existing_by_id:
                        fallback_char = NewCharacterSpec(
                            character_id=fallback_id,
                            canonical_name=speaker_id.replace("char_", "").replace("_", " ").title(),
                            aliases=[],
                            gender="neutral",
                            voice_instruct=(
                                "An adult mid-register voice with a clear, natural timbre "
                                "and steady conversational delivery."
                            ),
                            reference_sample_text=(
                                "I never expected things to turn out quite the way they did today."
                            ),
                        )
                        deduped_new_chars.append(fallback_char)
                        existing_by_id[fallback_id] = RegistryCharacterContext(
                            character_id=fallback_id,
                            canonical_name=fallback_char.canonical_name,
                            aliases=[],
                            gender="neutral",
                        )
                        name_to_existing_id[fallback_char.canonical_name.lower().strip()] = fallback_id
                    speaker_id = fallback_id

            cleaned_segments.append(
                ScriptSegmentSpec(
                    segment_order=len(cleaned_segments),
                    paragraph_index=seg.paragraph_index,
                    speaker_id=speaker_id,
                    text=cleaned_text,
                    is_paragraph_end=False,
                )
            )

        if not cleaned_segments:
            raise ValueError("LLM returned zero non-empty segments.")

        # Enforce exact is_paragraph_end flags per paragraph_index group
        for idx, seg in enumerate(cleaned_segments):
            is_last_in_chunk = (idx == len(cleaned_segments) - 1)
            next_diff_para = (
                not is_last_in_chunk
                and cleaned_segments[idx + 1].paragraph_index != seg.paragraph_index
            )
            seg.is_paragraph_end = is_last_in_chunk or next_diff_para

        # Consecutive same-index fragments are ordered source, never dictionary keys.
        sources: list[tuple[int, str]] = []
        for paragraph in paragraphs:
            if sources and sources[-1][0] == paragraph.paragraph_index:
                index, text = sources[-1]
                sources[-1] = (index, text + " " + paragraph.text.strip())
            else:
                sources.append((paragraph.paragraph_index, paragraph.text.strip()))
        source_index = 0
        offset = 0
        for segment in cleaned_segments:
            while source_index < len(sources) and not sources[source_index][1][offset:].strip():
                source_index += 1
                offset = 0
            if source_index >= len(sources):
                raise ValueError("Prose fidelity: extra output after source end.")
            paragraph_index, source = sources[source_index]
            if segment.paragraph_index != paragraph_index:
                raise ValueError(
                    f"Prose fidelity: expected paragraph {paragraph_index}, got {segment.paragraph_index}."
                )
            boundary_start = offset
            while offset < len(source) and source[offset].isspace():
                offset += 1
            if not source.startswith(segment.text, offset):
                raise ValueError(
                    f"Prose fidelity: paragraph {paragraph_index}, source group {source_index}, offset {offset} mismatch."
                )
            offset += len(segment.text)
            segment.text = source[boundary_start:offset]
            # Splitting a word would allow changed word boundaries after rendering/TTS.
            if offset < len(source) and (
                (source[offset - 1].isalnum() and source[offset].isalnum())
                or (source[offset - 1].isalnum() and source[offset] == "'"
                    and offset + 1 < len(source) and source[offset + 1].isalnum())
                or (source[offset - 1] == "'" and offset >= 2
                    and source[offset - 2].isalnum() and source[offset].isalnum())
            ):
                raise ValueError(f"Prose fidelity: split word in paragraph {paragraph_index} at offset {offset}.")
        if source_index < len(sources) and sources[source_index][1][offset:].strip():
            raise ValueError(f"Prose fidelity: incomplete paragraph {sources[source_index][0]} at offset {offset}.")
        if any(text.strip() for _, text in sources[source_index + 1:]):
            raise ValueError("Prose fidelity: missing source paragraphs.")

        return ChunkAnalysisResult(
            new_characters=deduped_new_chars,
            character_updates=updates,
            segments=cleaned_segments,
        )

    @staticmethod
    def _build_deterministic_fallback(
        paragraphs: Sequence[ChunkParagraphInput],
    ) -> ChunkAnalysisResult:
        """Emergency fallback that preserves 100% of prose under the narrator voice."""
        segments = [
            ScriptSegmentSpec(
                segment_order=idx,
                paragraph_index=p.paragraph_index,
                speaker_id=DEFAULT_NARRATOR_ID,
                text=p.text.strip(),
                is_paragraph_end=True,
            )
            for idx, p in enumerate(paragraphs)
            if p.text.strip()
        ]
        return ChunkAnalysisResult(
            new_characters=[],
            character_updates=[],
            segments=segments,
        )

