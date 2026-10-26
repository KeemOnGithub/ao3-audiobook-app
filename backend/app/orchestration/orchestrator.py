"""Job Orchestrator coordinating Ingestion, Sequential LLM Scripting, TTS PriorityQueue, and Mastering per Spec 04."""

import asyncio
from collections import defaultdict
import logging
import os
import shutil
import uuid
from typing import Dict, List, Optional, Union
import httpx

from backend.app.db.models import ChapterDTO, CharacterDTO, SegmentDTO, SegmentClaim, MasterClaim
from backend.app.db.repository import AudiobookRepository
from backend.app.db.review_repository import ReviewConflict
from backend.app.ingestion.service import ingest_ao3_chapter
from backend.app.ingestion.schemas import IngestionResult
from backend.app.ingestion.url_parser import parse_ao3_url
from backend.app.orchestration.event_bus import ChapterEventBus
from backend.app.orchestration.queue_models import TTSTask
from backend.app.scripting.analyzer import LLMOutputTruncated, LLMScriptAnalyzer
from backend.app.scripting.schemas import (
    ChunkParagraphInput,
    PreviousSegmentContext,
    RegistryCharacterContext,
)

logger = logging.getLogger(__name__)



class JobOrchestrator:
    """Central async coordinator for the AO3 Audiobook pipeline."""

    def __init__(
        self,
        repository: AudiobookRepository,
        event_bus: ChapterEventBus,
        llm_analyzer: LLMScriptAnalyzer,
        tts_base_url: str = "http://localhost:8002",
        tts_concurrency: int = 2,
        data_dir: str = "./data",
        tts_http_client: Optional[httpx.AsyncClient] = None,
    ):
        self.repo = repository
        self.event_bus = event_bus
        self.llm_analyzer = llm_analyzer
        self.tts_base_url = tts_base_url.rstrip("/")
        self.tts_concurrency = tts_concurrency
        self.data_dir = data_dir
        self.tts_priority_queue: asyncio.PriorityQueue[TTSTask] = asyncio.PriorityQueue()
        self.char_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._ingestion_locks: Dict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)
        self._load_lock = asyncio.Lock()
        self._chapter_requests: Dict[str, set[asyncio.Task]] = defaultdict(set)
        self._worker_tasks: List[asyncio.Task] = []
        self._scripting_tasks: Dict[str, asyncio.Task] = {}
        self._mastering_tasks: Dict[str, asyncio.Task] = {}
        self._preview_tasks: Dict[str, asyncio.Task] = {}
        self._external_tts_client = tts_http_client
        self._is_running = False

    async def start(self) -> None:
        """Start background TTS workers."""
        self._is_running = True
        for i in range(self.tts_concurrency):
            t = asyncio.create_task(self._tts_worker_loop(worker_id=i))
            self._worker_tasks.append(t)

    async def stop(self) -> None:
        """Cancel and drain workers, scripting jobs, and mastering jobs."""
        self._is_running = False
        all_tasks = list(self._worker_tasks) + list(self._scripting_tasks.values()) + list(self._mastering_tasks.values()) + list(self._preview_tasks.values())
        for t in all_tasks:
            t.cancel()
        await asyncio.gather(*all_tasks, return_exceptions=True)
        self._worker_tasks.clear()
        self._scripting_tasks.clear()
        self._mastering_tasks.clear()
        self._preview_tasks.clear()

    async def ingest_chapter(self, url: str, force_regenerate: Union[bool, str] = False, html_override: Optional[str] = None) -> ChapterDTO:
        """Stage 1: Fetch and parse AO3 chapter, initialize DB records and sequential LLM pipeline."""
        if force_regenerate == 'AUDIO_ONLY':
            work_id,chapter_id=parse_ao3_url(url)
            async with self._load_lock:
                async with self.repo.db_manager.get_connection() as conn:
                    cursor=await conn.execute('SELECT chapter_uuid FROM chapters WHERE source_url=? OR (work_id=? AND chapter_id=?) ORDER BY updated_at DESC LIMIT 1',(url,work_id,chapter_id))
                    row=await cursor.fetchone()
                if not row:
                    raise ReviewConflict('Audio-only regeneration requires a saved complete script','INCOMPLETE_ANALYSIS')
                chapter=await self.repo.get_chapter_by_uuid(row['chapter_uuid'])
                await self.repo.return_chapter_to_review(chapter.chapter_uuid,chapter.run_generation,chapter.review_revision)
                await self.cancel_chapter_execution(chapter.chapter_uuid)
                await self.emit_review_changed(chapter.chapter_uuid,'audio_only_review')
                return await self.repo.get_chapter_by_uuid(chapter.chapter_uuid)
        # 1. Ingest via Spec 01 pipeline
        result = await ingest_ao3_chapter(raw_url=url, html_override=html_override)
        meta = result.metadata

        async with self._load_lock:
            existing = await self.repo.get_chapter_by_work_and_id(meta.work_id, meta.chapter_id)
            superseded = await self.repo.supersede_active_chapters(
                existing.chapter_uuid if existing else ""
            )
            cancelled = []
            for chapter_uuid in superseded:
                for tasks in (self._scripting_tasks, self._mastering_tasks):
                    task = tasks.pop(chapter_uuid, None)
                    if task:
                        task.cancel()
                        cancelled.append(task)
                for task in list(self._chapter_requests.get(chapter_uuid, ())):
                    task.cancel()
                    cancelled.append(task)
                await self.cancel_voice_previews(chapter_uuid)
            await asyncio.gather(*cancelled, return_exceptions=True)
            return await self._initialize_chapter(result, url, force_regenerate)

    async def _chapter_post(self, chapter_uuid: str, generation: int, client, url: str, **kwargs):
        """Cancel local HTTP waits on supersession; remote execution is not guaranteed to stop."""
        chapter = await self.repo.get_chapter_by_uuid(chapter_uuid)
        allowed = chapter and chapter.approval_valid and chapter.review_phase in ('SYNTHESIZING','MASTERING')
        if not allowed or chapter.run_generation != generation:
            raise asyncio.CancelledError
        task = asyncio.create_task(client.post(url, **kwargs))
        self._chapter_requests[chapter_uuid].add(task)
        try:
            return await task
        finally:
            self._chapter_requests[chapter_uuid].discard(task)

    async def _retry_tts_task(self, task: TTSTask, delay: float) -> None:
        wait = asyncio.create_task(asyncio.sleep(delay))
        self._chapter_requests[task.chapter_uuid].add(wait)
        try:
            await wait
            chapter = await self.repo.get_chapter_by_uuid(task.chapter_uuid)
            if chapter and chapter.run_generation == task.run_generation and chapter.narration_authorized and chapter.review_revision == task.approved_review_revision:
                await self.tts_priority_queue.put(task)
        finally:
            self._chapter_requests[task.chapter_uuid].discard(wait)

    async def _initialize_chapter(self, result: IngestionResult, url: str, force_regenerate: Union[bool, str]) -> ChapterDTO:
        meta = result.metadata

        # 2. Upsert work and chapter in DB
        await self.repo.upsert_work(
            work_id=meta.work_id,
            title=meta.title,
            author=meta.author,
            fandoms=meta.fandoms,
        )

        existing_chapter = await self.repo.get_chapter_by_work_and_id(meta.work_id, meta.chapter_id)
        if existing_chapter and force_regenerate == "AUDIO_ONLY":
            # Re-synthesize audio only: advance generation and reset segments
            new_gen = existing_chapter.run_generation + 1
            updated_ch = await self.repo.replace_chapter_generation(
                chapter_uuid=existing_chapter.chapter_uuid,
                new_generation=new_gen,
                mode="AUDIO_ONLY",
            )
            chapter = updated_ch or existing_chapter
            await self.cancel_chapter_execution(chapter.chapter_uuid)
            return chapter

        # Every normal URL submission starts fresh. Only AUDIO_ONLY preserves profiles.
        if existing_chapter:
            new_gen = existing_chapter.run_generation + 1
            updated_ch = await self.repo.replace_chapter_generation(existing_chapter.chapter_uuid,new_gen,len(result.chunks),'FULL')
            await self.cancel_chapter_execution(existing_chapter.chapter_uuid)
            chapter=updated_ch or existing_chapter
            old_tasks = [
                tasks.pop(existing_chapter.chapter_uuid, None)
                for tasks in (self._scripting_tasks, self._mastering_tasks)
            ]
            for old_task in old_tasks:
                if old_task and not old_task.done():
                    old_task.cancel()
            await asyncio.gather(*(task for task in old_tasks if task), return_exceptions=True)

        else:
            chapter = await self.repo.upsert_chapter(
                work_id=meta.work_id,
                chapter_id=meta.chapter_id,
                chapter_number=meta.chapter_index,
                chapter_title=meta.chapter_title,
                source_url=url,
                total_chunks=len(result.chunks),
                voice_scope=str(uuid.uuid4()),
            )

        await self.repo.ensure_default_narrator(meta.work_id, chapter.voice_scope)

        # 3. Spawn Stage 2 sequential LLM task with handle tracking
        t = asyncio.create_task(
            self._run_chapter_llm_pipeline(chapter.chapter_uuid, meta.work_id, result.chunks, chapter.run_generation)
        )
        self._scripting_tasks[chapter.chapter_uuid] = t

        return chapter

    async def _run_chapter_llm_pipeline(self, chapter_uuid: str, work_id: str, chunks: list, run_generation: int = 0) -> None:
        """Stage 2: Sequential LLM script deconstruction maintaining rolling character registry."""
        try:
            chapter = await self.repo.get_chapter_by_uuid(chapter_uuid)
            if not chapter or chapter.run_generation != run_generation:
                return
            voice_scope = chapter.voice_scope
            async with self.repo.db_manager.write_lock:
                async with self.repo.db_manager.get_connection() as conn:
                    cursor = await conn.execute(
                        """
                        UPDATE chapters SET
                            llm_completed_chunks = 0,
                            total_segments = 0,
                            completed_segments = 0,
                            status = 'STREAMING',
                            error_message = NULL,
                            state_revision = state_revision + 1,
                            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                        WHERE chapter_uuid = :ch_uuid AND run_generation = :run_generation AND review_phase='ANALYZING';
                        """,
                        {"ch_uuid": chapter_uuid, "run_generation": run_generation},
                    )
                    await conn.commit()
                    if cursor.rowcount != 1:
                        return

            previous_context: List[PreviousSegmentContext] = []
            global_segment_order = 0

            for chunk_idx, chunk in enumerate(chunks):
                chapter = await self.repo.get_chapter_by_uuid(chapter_uuid)
                if not chapter or chapter.run_generation != run_generation:
                    return
                registered_chars = await self.repo.get_characters_for_work(work_id, voice_scope)
                registry_context = [
                    RegistryCharacterContext(
                        character_id=c.character_key,
                        canonical_name=c.canonical_name,
                        aliases=c.aliases,
                        gender="neutral",
                    )
                    for c in registered_chars
                ]

                # Convert chunk paragraphs to input format
                para_inputs = [
                    ChunkParagraphInput(paragraph_index=p.paragraph_index, text=p.text)
                    for p in chunk.paragraphs
                ]

                # Call LLM Script Analyzer
                analysis = await self.llm_analyzer.analyze_chunk(
                    chunk_index=chunk_idx,
                    paragraphs=para_inputs,
                    existing_characters=registry_context,
                    previous_context_segments=previous_context,
                )
                # Ingestion splits oversized paragraphs at sentence whitespace.
                # Restore that boundary when the same paragraph crosses chunks.
                if (
                    chunk_idx > 0
                    and analysis.segments
                    and chunks[chunk_idx - 1].paragraphs
                    and chunk.paragraphs
                    and chunks[chunk_idx - 1].paragraphs[-1].paragraph_index
                    == chunk.paragraphs[0].paragraph_index
                ):
                    analysis.segments[0].text = " " + analysis.segments[0].text.lstrip()
                if (
                    analysis.segments
                    and chunk_idx + 1 < len(chunks)
                    and chunk.paragraphs
                    and chunks[chunk_idx + 1].paragraphs
                    and chunk.paragraphs[-1].paragraph_index
                    == chunks[chunk_idx + 1].paragraphs[0].paragraph_index
                ):
                    analysis.segments[-1].is_paragraph_end = False
                chapter = await self.repo.get_chapter_by_uuid(chapter_uuid)
                if not chapter or chapter.run_generation != run_generation:
                    return

                # Upsert newly discovered characters
                char_key_to_uuid = {c.character_key: c.character_uuid for c in registered_chars}

                for new_char in analysis.new_characters:
                    if new_char.character_id not in char_key_to_uuid:
                        created = await self.repo.insert_character(
                            work_id=work_id,
                            character_key=new_char.character_id,
                            canonical_name=new_char.canonical_name,
                            aliases=new_char.aliases,
                            voice_instruct=new_char.voice_instruct,
                            reference_text=new_char.reference_sample_text,
                            voice_status="PENDING",
                            voice_scope=voice_scope,
                        )
                        char_key_to_uuid[new_char.character_id] = created.character_uuid

                # Spec 07: Apply character_updates to repository before processing segments
                for char_up in analysis.character_updates:
                    c_uuid = char_key_to_uuid.get(char_up.character_id)
                    if c_uuid:
                        existing_c = next((c for c in registered_chars if c.character_uuid == c_uuid), None)
                        if existing_c:
                            merged_aliases = list(dict.fromkeys(existing_c.aliases + char_up.new_aliases))
                            await self.repo.update_character_voice(
                                character_uuid=c_uuid,
                                voice_instruct=existing_c.voice_instruct,
                                voice_status=existing_c.voice_status,
                            )
                            # Update alias list in db
                            async with self.repo.db_manager.write_lock:
                                async with self.repo.db_manager.get_connection() as conn:
                                    import json
                                    await conn.execute(
                                        "UPDATE characters SET aliases_json = :aliases, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE character_uuid = :c_uuid;",
                                        {"aliases": json.dumps(merged_aliases), "c_uuid": c_uuid},
                                    )
                                    await conn.commit()

                # Refresh character mapping
                all_chars = await self.repo.get_characters_for_work(work_id, voice_scope)
                char_key_to_uuid = {c.character_key: c.character_uuid for c in all_chars}
                char_name_map = {c.character_uuid: c.canonical_name for c in all_chars}
                char_version_map = {c.character_uuid: c.voice_version for c in all_chars}
                narrator_char = next((c for c in all_chars if c.character_key == "char_narrator"), all_chars[0])
                narrator_dto = next((c for c in all_chars if c.character_key == "char_narrator"), None)
                narrator_ver = narrator_dto.voice_version if narrator_dto else 1

                # Prepare segments for DB insertion
                db_segments = []
                for seg in analysis.segments:
                    char_uuid = char_key_to_uuid.get(seg.speaker_id, narrator_char.character_uuid)
                    current_ver = char_version_map.get(char_uuid, narrator_ver)
                    db_segments.append(
                        {
                            "chapter_uuid": chapter_uuid,
                            "chunk_index": chunk_idx,
                            "segment_order": global_segment_order,
                            "paragraph_index": getattr(seg, "paragraph_index", None),
                            "source_continuation": 1 if getattr(seg, "source_continuation", False) else 0,
                            "character_uuid": char_uuid,
                            "text_content": seg.text,
                            "is_paragraph_end": 1 if seg.is_paragraph_end else 0,
                            "voice_version_used": current_ver,
                        }
                    )
                    global_segment_order += 1

                chapter = await self.repo.get_chapter_by_uuid(chapter_uuid)
                if not chapter or chapter.run_generation != run_generation:
                    return
                inserted_segments = await self.repo.bulk_insert_segments(db_segments, expected_generation=run_generation, completed_chunk=chunk_idx)
                if not inserted_segments:
                    raise ValueError('Analysis returned an empty chunk or was superseded')
                current_revision = (await self.repo.get_chapter_by_uuid(chapter_uuid)).state_revision

                # Emit script_chunk_ready and chunk_scripted SSE events with full envelope per Spec 07
                chunk_payload = {
                    "chapter_uuid": chapter_uuid,
                    "run_generation": run_generation,
                    "state_revision": current_revision,
                    "chunk_index": chunk_idx,
                    "total_chunks": len(chunks),
                    "total_segments": global_segment_order,
                    "total_segments_so_far": global_segment_order,
                    "segments": [
                        {
                            "segment_uuid": s.segment_uuid,
                            "segment_order": s.segment_order,
                            "chunk_index": s.chunk_index,
                            "paragraph_index": s.paragraph_index,
                            "source_continuation": bool(s.source_continuation),
                            "character_uuid": s.character_uuid,
                            "speaker_key": next((k for k, v in char_key_to_uuid.items() if v == s.character_uuid), "char_narrator"),
                            "speaker_name": char_name_map.get(s.character_uuid, "Narrator"),
                            "text": s.text_content,
                            "is_paragraph_end": bool(s.is_paragraph_end),
                            "voice_version": s.voice_version_used,
                        }
                        for s in inserted_segments
                    ],
                }
                await self.event_bus.emit(
                    chapter_uuid=chapter_uuid,
                    event_type="script_chunk_ready",
                    payload=chunk_payload,
                    run_generation=run_generation,
                    state_revision=current_revision,
                )
                await self.event_bus.emit(
                    chapter_uuid=chapter_uuid,
                    event_type="chunk_scripted",
                    payload=chunk_payload,
                    run_generation=run_generation,
                    state_revision=current_revision,
                )

                # Update lookback context for next chunk
                previous_context = [
                    PreviousSegmentContext(speaker_id=s.speaker_id, text=s.text)
                    for s in analysis.segments[-3:]
                ]

            await self.repo.finalize_chapter_review(chapter_uuid, run_generation)
            await self.emit_review_changed(chapter_uuid, 'analysis_completed')

        except Exception as exc:
            error_code = "LLM_OUTPUT_TRUNCATED" if isinstance(exc, LLMOutputTruncated) else "LLM_DECONSTRUCTION_FAILED"
            logger.error("LLM pipeline failed for chapter %s: %s", chapter_uuid, exc)
            await self.repo.transition_chapter_failed(
                chapter_uuid=chapter_uuid,
                run_generation=run_generation,
                code=error_code,
                message=str(exc),
            )
            ch = await self.repo.get_chapter_by_uuid(chapter_uuid)
            rev = ch.state_revision if ch else 0
            await self.event_bus.emit(
                chapter_uuid=chapter_uuid,
                event_type="chapter_failed",
                payload={
                    "chapter_uuid": chapter_uuid,
                    "status": "FAILED",
                    "error_code": error_code,
                    "error_message": str(exc),
                },
                run_generation=run_generation,
                state_revision=rev,
            )


    async def _ensure_character_voice(self, character: CharacterDTO, chapter_uuid: str = '', generation: int = -1) -> None:
        """Call TTS microservice /internal/tts/create-voice and update character to READY."""
        async with self.char_locks[character.character_uuid]:
            chapter = await self.repo.get_chapter_by_uuid(chapter_uuid)
            character = await self.repo.get_character_by_uuid(character.character_uuid)
            if not chapter or not chapter.narration_authorized or chapter.run_generation != generation:
                raise ReviewConflict('Narration is no longer approved', 'NOT_AUTHORIZED')
            if character.voice_status == "READY" and character.embedding_pt_path and os.path.isfile(character.embedding_pt_path):
                return
            if character.preview_mp3_path:
                raise RuntimeError('Applied auditioned voice artifact is unavailable; return to review to select a replacement.')

            client = self._external_tts_client or httpx.AsyncClient(timeout=180.0)
            should_close = self._external_tts_client is None
            try:
                res = await self._chapter_post(chapter_uuid, generation, client,
                    f"{self.tts_base_url}/internal/tts/create-voice",
                    json={
                        "work_id": character.work_id,
                        "character_id": f'{character.character_uuid}_g{generation}',
                        "version": character.voice_version,
                        "voice_instruct": character.voice_instruct,
                        "reference_sample_text": character.reference_text,
                    },
                )
                res.raise_for_status()
                data = res.json()
                async with self.repo.db_manager.write_lock:
                    async with self.repo.db_manager.get_connection() as conn:
                        cursor = await conn.execute("""UPDATE characters SET voice_status='READY',embedding_pt_path=?,preview_mp3_path=?,
                            artifact_provider=?,artifact_fingerprint=? WHERE character_uuid=? AND voice_version=? AND voice_instruct=?
                            AND EXISTS(SELECT 1 FROM chapters ch WHERE ch.chapter_uuid=? AND ch.run_generation=?
                                AND ch.review_phase='SYNTHESIZING' AND ch.approved_run_generation=ch.run_generation
                                AND ch.approved_review_revision=ch.review_revision AND ch.approved_at IS NOT NULL)""",
                            (data['embedding_path'],data['preview_audio_path'],data.get('artifact_provider'),data.get('artifact_fingerprint'),
                             character.character_uuid,character.voice_version,character.voice_instruct,chapter_uuid,generation))
                        await conn.commit()
                        if cursor.rowcount != 1:
                            return

                preview_web_url = f"/api/v1/audio/previews/{character.character_uuid}.mp3?v={character.voice_version}"
                char_payload = {
                    "character_uuid": character.character_uuid,
                    "character_key": character.character_key,
                    "canonical_name": character.canonical_name,
                    "voice_version": character.voice_version,
                    "preview_url": preview_web_url,
                    "voice_status": "READY",
                    "voice_instruct": character.voice_instruct,
                    "aliases": character.aliases,
                    "is_narrator": bool(character.is_narrator),
                }
                async with self.repo.db_manager.write_lock:
                    async with self.repo.db_manager.get_connection() as conn:
                        await conn.execute(
                            """
                            UPDATE chapters SET state_revision = state_revision + 1
                            WHERE work_id = ? AND voice_scope = ?;
                            """,
                            (character.work_id, character.voice_scope),
                        )
                        cursor = await conn.execute(
                            "SELECT chapter_uuid, run_generation, state_revision FROM chapters WHERE work_id = ? AND voice_scope = ?;",
                            (character.work_id, character.voice_scope),
                        )
                        chapters = await cursor.fetchall()
                        await conn.commit()
                for chapter in chapters:
                    for event_type in ("character_ready", "character_voice_ready"):
                        await self.event_bus.emit(
                            chapter_uuid=chapter["chapter_uuid"],
                            event_type=event_type,
                            payload=dict(char_payload),
                            run_generation=chapter["run_generation"],
                            state_revision=chapter["state_revision"],
                        )
            finally:
                if should_close:
                    await client.aclose()

    async def _tts_worker_loop(self, worker_id: int) -> None:
        """Stage 4: Asynchronous worker processing TTSTask items from PriorityQueue per Spec 07."""
        worker_token = f"worker_{worker_id}_{uuid.uuid4().hex[:8]}"

        while self._is_running:
            try:
                task = await self.tts_priority_queue.get()
            except asyncio.CancelledError:
                break

            try:
                # Step 1: Pre-synthesis guard & atomic segment claim
                claim = await self.repo.claim_segment(
                    segment_uuid=task.segment_uuid,
                    worker_token=worker_token,
                    run_generation=task.run_generation,
                    target_voice_version=task.target_voice_version,
                    character_uuid=task.character_uuid,
                    approved_review_revision=task.approved_review_revision,
                )

                if not claim:
                    # Task is stale or superseded by generation/version bump or another worker
                    continue

                # Fetch character voice details
                char = await self.repo.get_character_by_uuid(task.character_uuid)
                ch = await self.repo.get_chapter_by_uuid(task.chapter_uuid)
                seg = await self.repo.get_segment_by_uuid(task.segment_uuid)

                if not char or not ch or not seg:
                    continue

                if not ch.narration_authorized:
                    continue

                # Ensure character embedding exists
                embedding_path = char.embedding_pt_path
                if not embedding_path or not os.path.exists(embedding_path):
                    voice_task = asyncio.create_task(self._ensure_character_voice(char, task.chapter_uuid, task.run_generation))
                    self._chapter_requests[task.chapter_uuid].add(voice_task)
                    try:
                        await voice_task
                    finally:
                        self._chapter_requests[task.chapter_uuid].discard(voice_task)
                    char_refreshed = await self.repo.get_character_by_uuid(task.character_uuid)
                    embedding_path = char_refreshed.embedding_pt_path if char_refreshed else None

                current = await self.repo.get_chapter_by_uuid(task.chapter_uuid)
                current_segment = await self.repo.get_segment_by_uuid(task.segment_uuid)
                current_char = await self.repo.get_character_by_uuid(task.character_uuid)
                if (not current or not current.narration_authorized or current.run_generation != task.run_generation
                        or current.review_revision != task.approved_review_revision or not current_segment
                        or current_segment.character_uuid != task.character_uuid or not current_char
                        or current_char.voice_version != task.target_voice_version):
                    continue

                # Step 2: Synthesis execution
                client = self._external_tts_client or httpx.AsyncClient(timeout=180.0)
                should_close = self._external_tts_client is None
                try:
                    synth_res = await self._chapter_post(
                        task.chapter_uuid, task.run_generation, client,
                        f"{self.tts_base_url}/internal/tts/synthesize-line",
                        json={
                            "chapter_id": task.chapter_uuid,
                            "segment_id": task.segment_uuid,
                            "order": task.segment_order,
                            "version": task.target_voice_version,
                            "text": seg.text_content,
                            "embedding_path": embedding_path,
                            "expected_artifact_provider": current_char.artifact_provider,
                            "expected_artifact_fingerprint": current_char.artifact_fingerprint,
                        },
                    )
                    synth_res.raise_for_status()
                    synth_data = synth_res.json()
                except httpx.HTTPStatusError as exc:
                    status_code = exc.response.status_code
                    # Non-retryable: 400, 401, 403, unsupported route, corrupt response
                    retryable = status_code in (429, 500, 502, 503, 504)
                    result = await self.repo.record_segment_retry_or_failure(
                        segment_uuid=task.segment_uuid,
                        claim=claim,
                        error_code=f"HTTP_{status_code}",
                        message=str(exc),
                        retryable=retryable,
                    )
                    if result.status == "QUEUED":
                        # Re-enqueue with backoff
                        backoffs = [1.0, 3.0, 10.0]
                        delay = backoffs[min(result.retry_count - 1, len(backoffs) - 1)]
                        await self._retry_tts_task(task, delay)
                    else:
                        ch = await self.repo.get_chapter_by_uuid(task.chapter_uuid)
                        rev = ch.state_revision if ch else 0
                        await self.event_bus.emit(
                            chapter_uuid=task.chapter_uuid,
                            event_type="segment_failed",
                            payload={
                                "segment_uuid": task.segment_uuid,
                                "status": "FAILED",
                                "retry_count": result.retry_count,
                                "error_code": result.error_code,
                                "error_message": result.error_message,
                            },
                            run_generation=task.run_generation,
                            state_revision=rev,
                        )
                    continue
                except (httpx.TimeoutException, httpx.NetworkError, IOError) as exc:
                    # Retryable network/IO error
                    result = await self.repo.record_segment_retry_or_failure(
                        segment_uuid=task.segment_uuid,
                        claim=claim,
                        error_code="NETWORK_OR_IO_ERROR",
                        message=str(exc),
                        retryable=True,
                    )
                    if result.status == "QUEUED":
                        backoffs = [1.0, 3.0, 10.0]
                        delay = backoffs[min(result.retry_count - 1, len(backoffs) - 1)]
                        await self._retry_tts_task(task, delay)
                    else:
                        ch = await self.repo.get_chapter_by_uuid(task.chapter_uuid)
                        rev = ch.state_revision if ch else 0
                        await self.event_bus.emit(
                            chapter_uuid=task.chapter_uuid,
                            event_type="segment_failed",
                            payload={
                                "segment_uuid": task.segment_uuid,
                                "status": "FAILED",
                                "retry_count": result.retry_count,
                                "error_code": result.error_code,
                                "error_message": result.error_message,
                            },
                            run_generation=task.run_generation,
                            state_revision=rev,
                        )
                    continue
                except Exception as exc:
                    result = await self.repo.record_segment_retry_or_failure(
                        segment_uuid=task.segment_uuid,
                        claim=claim,
                        error_code="UNEXPECTED_ERROR",
                        message=str(exc),
                        retryable=False,
                    )
                    ch = await self.repo.get_chapter_by_uuid(task.chapter_uuid)
                    rev = ch.state_revision if ch else 0
                    await self.event_bus.emit(
                        chapter_uuid=task.chapter_uuid,
                        event_type="segment_failed",
                        payload={
                            "segment_uuid": task.segment_uuid,
                            "status": "FAILED",
                            "retry_count": result.retry_count,
                            "error_code": result.error_code,
                            "error_message": result.error_message,
                        },
                        run_generation=task.run_generation,
                        state_revision=rev,
                    )
                    continue
                finally:
                    if should_close:
                        await client.aclose()

                # Step 3: Collision-resistant target path and atomic move per Spec 07 section 5
                # {DATA_DIR}/works/{work_id}/chapters/{chapter_uuid}/g{run_generation}/segments/seg_{segment_uuid}_v{voice_version}.mp3
                final_dir = os.path.join(
                    self.data_dir,
                    "works",
                    ch.work_id,
                    "chapters",
                    task.chapter_uuid,
                    f"g{task.run_generation}",
                    "segments",
                )
                os.makedirs(final_dir, exist_ok=True)
                final_audio_path = os.path.join(
                    final_dir,
                    f"seg_{task.segment_uuid}_v{task.target_voice_version}.mp3",
                )

                src_audio = synth_data["audio_path"]
                if os.path.abspath(src_audio) != os.path.abspath(final_audio_path):
                    # Write to temporary file first then replace
                    temp_target = f"{final_audio_path}.tmp.{uuid.uuid4().hex}"
                    shutil.copyfile(src_audio, temp_target)
                    os.replace(temp_target, final_audio_path)
                    try:
                        os.remove(src_audio)
                    except OSError:
                        pass
                else:
                    final_audio_path = src_audio

                # Step 4: Atomic commit matching claim
                committed = await self.repo.commit_segment_ready(
                    segment_uuid=task.segment_uuid,
                    claim=claim,
                    audio_path=final_audio_path,
                    duration_ms=synth_data["duration_ms"],
                )

                if not committed:
                    # Claim superseded: clean up file
                    if os.path.exists(final_audio_path):
                        try:
                            os.remove(final_audio_path)
                        except OSError:
                            pass
                    continue

                # Emit segment_ready and segment_audio_ready with envelope
                ch_updated = await self.repo.get_chapter_by_uuid(task.chapter_uuid)
                rev = ch_updated.state_revision if ch_updated else 0
                audio_web_url = f"/api/v1/audio/segments/{task.segment_uuid}.mp3?v={task.target_voice_version}"
                seg_payload = {
                    "chapter_uuid": task.chapter_uuid,
                    "run_generation": task.run_generation,
                    "state_revision": rev,
                    "segment_uuid": task.segment_uuid,
                    "segment_order": task.segment_order,
                    "audio_url": audio_web_url,
                    "duration_ms": synth_data["duration_ms"],
                    "voice_version": task.target_voice_version,
                    "voice_version_used": task.target_voice_version,
                    "completed_segments": ch_updated.completed_segments if ch_updated else 0,
                    "total_segments": ch_updated.total_segments if ch_updated else 0,
                }
                await self.event_bus.emit(
                    chapter_uuid=task.chapter_uuid,
                    event_type="segment_ready",
                    payload=seg_payload,
                    run_generation=task.run_generation,
                    state_revision=rev,
                )
                await self.event_bus.emit(
                    chapter_uuid=task.chapter_uuid,
                    event_type="segment_audio_ready",
                    payload=seg_payload,
                    run_generation=task.run_generation,
                    state_revision=rev,
                )

                # Check Mastering Gate via claim_mastering
                master_claim = await self.repo.claim_mastering(
                    chapter_uuid=task.chapter_uuid,
                    run_generation=task.run_generation,
                )
                if master_claim:
                    ch_mast = await self.repo.get_chapter_by_uuid(task.chapter_uuid)
                    m_rev = ch_mast.state_revision if ch_mast else rev + 1
                    await self.event_bus.emit(
                        chapter_uuid=task.chapter_uuid,
                        event_type="chapter_mastering",
                        payload={"chapter_uuid": task.chapter_uuid, "status": "MASTERING", "progress_percent": 50},
                        run_generation=task.run_generation,
                        state_revision=m_rev,
                    )
                    await self.event_bus.emit(
                        chapter_uuid=task.chapter_uuid,
                        event_type="master_mp3_progress",
                        payload={"status": "MASTERING", "progress_percent": 50},
                        run_generation=task.run_generation,
                        state_revision=m_rev,
                    )
                    t_master = asyncio.create_task(
                        self._run_ffmpeg_mastering(task.chapter_uuid, master_claim)
                    )
                    self._mastering_tasks[task.chapter_uuid] = t_master

            except asyncio.CancelledError:
                if not self._is_running:
                    raise
            except Exception as exc:
                logger.error("TTS worker %d failed processing task: %s", worker_id, exc)
                if 'claim' in locals() and claim:
                    await self.repo.record_segment_retry_or_failure(task.segment_uuid,claim,'VOICE_OR_WORKER_FAILED',str(exc),False)
            finally:
                self.tts_priority_queue.task_done()

    async def _run_ffmpeg_mastering(self, chapter_uuid: str, claim: Union[MasterClaim, int]) -> None:
        """Stage 5: Stitch all completed segments into a master MP3 per Spec 07."""
        if not isinstance(claim, MasterClaim):
            return
        master_claim = claim

        try:
            chapter = await self.repo.get_chapter_by_uuid(chapter_uuid)
            if (not chapter or not chapter.approval_valid or chapter.review_phase != 'MASTERING'
                    or chapter.run_generation != claim.run_generation or chapter.master_version != claim.master_version
                    or chapter.review_revision != claim.approved_review_revision):
                return

            segments = await self.repo.get_segments_for_chapter(chapter_uuid)
            stitch_items = []
            for s in segments:
                pause = "paragraph_end" if s.is_paragraph_end else "intra_paragraph"
                stitch_items.append({"audio_path": s.audio_path, "pause_after": pause})

            client = self._external_tts_client or httpx.AsyncClient(timeout=180.0)
            should_close = self._external_tts_client is None
            try:
                res = await self._chapter_post(chapter_uuid, claim.run_generation, client,
                    f"{self.tts_base_url}/internal/tts/stitch-chapter",
                    json={
                        "work_id": chapter.work_id,
                        "chapter_id": chapter.chapter_id,
                        "segments": stitch_items,
                        "metadata": {
                            "title": chapter.chapter_title,
                            "author": "AO3 Author",
                            "work_name": chapter.chapter_title,
                            "chapter_number": chapter.chapter_number,
                        },
                    },
                )
                res.raise_for_status()
                data = res.json()
            finally:
                if should_close:
                    await client.aclose()

            # Spec 07: Validate master audio before atomic publication
            src_master = data["master_audio_path"]
            if not os.path.exists(src_master) or os.path.getsize(src_master) == 0:
                raise RuntimeError("Stitched master file does not exist or is empty")

            # Collision-resistant path:
            # {DATA_DIR}/works/{work_id}/chapters/{chapter_uuid}/g{run_generation}/master/master_v{master_version}.mp3
            final_dir = os.path.join(
                self.data_dir,
                "works",
                chapter.work_id,
                "chapters",
                chapter_uuid,
                f"g{master_claim.run_generation}",
                "master",
            )
            os.makedirs(final_dir, exist_ok=True)
            final_master_path = os.path.join(
                final_dir,
                f"master_v{master_claim.master_version}.mp3",
            )

            if os.path.abspath(src_master) != os.path.abspath(final_master_path):
                temp_target = f"{final_master_path}.tmp.{uuid.uuid4().hex}"
                shutil.copyfile(src_master, temp_target)
                os.replace(temp_target, final_master_path)
                try:
                    os.remove(src_master)
                except OSError:
                    pass
            else:
                final_master_path = src_master

            committed = await self.repo.commit_master_completed(
                chapter_uuid=chapter_uuid,
                claim=master_claim,
                master_mp3_path=final_master_path,
            )

            if committed:
                ch_done = await self.repo.get_chapter_by_uuid(chapter_uuid)
                rev = ch_done.state_revision if ch_done else 0
                master_download_url = f"/api/v1/chapters/{chapter_uuid}/download"
                await self.event_bus.emit(
                    chapter_uuid=chapter_uuid,
                    event_type="chapter_completed",
                    payload={
                        "chapter_uuid": chapter_uuid,
                        "status": "COMPLETED",
                        "master_mp3_url": master_download_url,
                    },
                    run_generation=master_claim.run_generation,
                    state_revision=rev,
                )
                await self.event_bus.emit(
                    chapter_uuid=chapter_uuid,
                    event_type="master_mp3_progress",
                    payload={
                        "status": "COMPLETED",
                        "progress_percent": 100,
                        "master_mp3_url": master_download_url,
                    },
                    run_generation=master_claim.run_generation,
                    state_revision=rev,
                )
        except Exception as exc:
            logger.error("Master stitching failed for %s: %s", chapter_uuid, exc)
            await self.repo.fail_mastering(
                chapter_uuid=chapter_uuid,
                claim=master_claim,
                code="MASTERING_FAILED",
                message=str(exc),
            )
            ch_fail = await self.repo.get_chapter_by_uuid(chapter_uuid)
            rev = ch_fail.state_revision if ch_fail else 0
            await self.event_bus.emit(
                chapter_uuid=chapter_uuid,
                event_type="chapter_failed",
                payload={
                    "chapter_uuid": chapter_uuid,
                    "status": "FAILED",
                    "error_code": "MASTERING_FAILED",
                    "error_message": str(exc),
                },
                run_generation=master_claim.run_generation,
                state_revision=rev,
            )


    async def handle_voice_hotswap(
        self,
        character_uuid: str,
        new_voice_instruct: str,
        current_chapter_uuid: str,
        current_playback_index: int = 0,
    ) -> CharacterDTO:
        """Voice Hot-Swap workflow per Spec 04 section 6.2."""
        raise ReviewConflict('Return to review and audition/apply a candidate instead of direct voice regeneration.', 'REOPEN_REVIEW_REQUIRED')

    async def emit_review_changed(self, chapter_uuid, reason):
        chapter = await self.repo.get_chapter_by_uuid(chapter_uuid)
        await self.event_bus.emit(chapter_uuid, 'chapter_review_changed',
            {'review_phase': chapter.review_phase, 'review_revision': chapter.review_revision, 'reason': reason},
            run_generation=chapter.run_generation,state_revision=chapter.state_revision)

    async def generate_voice_preview(self, candidate):
        chapter_uuid = candidate['chapter_uuid']
        candidate_uuid = candidate['candidate_uuid']
        if not await self.repo.update_voice_preview_candidate(candidate_uuid,'GENERATING'):
            return
        await self.emit_review_changed(chapter_uuid,'preview_generating')
        chapter = await self.repo.get_chapter_by_uuid(chapter_uuid)
        client = self._external_tts_client or httpx.AsyncClient(timeout=180)
        try:
            response = await client.post(f'{self.tts_base_url}/internal/tts/create-voice',json={
                'work_id':chapter.work_id,'character_id':candidate_uuid,'version':1,
                'allow_voice_recreation':False,
                'voice_instruct':candidate['voice_instruct'],'reference_sample_text':candidate['reference_text']})
            response.raise_for_status()
            data = response.json()
            if not os.path.isfile(data['embedding_path']) or not os.path.isfile(data['preview_audio_path']):
                raise ValueError('Preview service returned unavailable artifacts')
            await self.repo.update_voice_preview_candidate(candidate_uuid,'READY',data)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('Optional preview failed for %s',candidate_uuid)
            await self.repo.update_voice_preview_candidate(candidate_uuid,'FAILED',{
                'error_code':'PREVIEW_FAILED','error_message':'Voice preview failed. Your applied voice is unchanged; try a new preview.'})
        finally:
            if self._external_tts_client is None:
                await client.aclose()
        await self.emit_review_changed(chapter_uuid,'preview_changed')

    def start_voice_preview(self, candidate):
        task = asyncio.create_task(self.generate_voice_preview(candidate))
        self._preview_tasks[candidate['candidate_uuid']] = task
        task.add_done_callback(lambda _: self._preview_tasks.pop(candidate['candidate_uuid'],None))

    async def cancel_voice_previews(self, chapter_uuid):
        candidates = await self.repo.get_voice_preview_candidates(chapter_uuid)
        tasks = [self._preview_tasks[c['candidate_uuid']] for c in candidates if c['candidate_uuid'] in self._preview_tasks]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)

    async def cancel_chapter_execution(self, chapter_uuid):
        await self.cancel_voice_previews(chapter_uuid)
        tasks = list(self._chapter_requests.get(chapter_uuid, ()))
        for mapping in (self._scripting_tasks,self._mastering_tasks):
            task = mapping.pop(chapter_uuid,None)
            if task:
                tasks.append(task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)

    async def schedule_approved_chapter(self, chapter_uuid, master_if_ready=True, playback_order=None):
        chapter = await self.repo.get_chapter_by_uuid(chapter_uuid)
        if not chapter or not chapter.narration_authorized:
            return
        cast = {c.character_uuid:c for c in await self.repo.get_characters_for_chapter(chapter_uuid)}
        for segment in await self.repo.get_segments_for_chapter(chapter_uuid):
            if segment.status == 'QUEUED' and segment.character_uuid in cast:
                distance=segment.segment_order if playback_order is None else abs(segment.segment_order-playback_order)+(1000 if segment.segment_order<playback_order else 0)
                await self.tts_priority_queue.put(TTSTask(priority_tier=2 if playback_order is None else 1,distance_key=distance,
                    chapter_uuid=chapter_uuid,run_generation=chapter.run_generation,
                    approved_review_revision=chapter.review_revision,segment_uuid=segment.segment_uuid,
                    segment_order=segment.segment_order,character_uuid=segment.character_uuid,
                    target_voice_version=cast[segment.character_uuid].voice_version))
        claim = await self.repo.claim_mastering(chapter_uuid,chapter.run_generation) if master_if_ready else None
        if claim:
            self._mastering_tasks[chapter_uuid] = asyncio.create_task(self._run_ffmpeg_mastering(chapter_uuid,claim))

    async def recover_interrupted_jobs(self) -> None:
        """Recover only durably authorized work; incomplete analysis never resumes."""
        async with self.repo.db_manager.write_lock:
            async with self.repo.db_manager.get_connection() as conn:
                await conn.execute("""UPDATE voice_preview_candidates SET status='FAILED',error_code='INTERRUPTED',
                    error_message='Preview interrupted by restart; generate a new candidate.' WHERE status IN ('PENDING','GENERATING')""")
                await conn.execute("""UPDATE chapters SET review_phase='FAILED',status='FAILED',
                    error_message='Reload chapter to finish analysis after server restart.',state_revision=state_revision+1
                    WHERE review_phase='ANALYZING'""")
                await conn.execute("""UPDATE chapters SET review_phase='FAILED',status='FAILED',
                    error_message='Execution has no matching approval; return to review.',state_revision=state_revision+1
                    WHERE review_phase IN ('SYNTHESIZING','MASTERING') AND
                    (analysis_completed_at IS NULL OR approved_at IS NULL OR approved_run_generation IS NULL
                    OR approved_review_revision IS NULL OR approved_run_generation!=run_generation OR approved_review_revision!=review_revision)""")
                await conn.execute("""UPDATE segments SET status='QUEUED',claimed_by=NULL,claimed_generation=NULL,claimed_at=NULL
                    WHERE status='SYNTHESIZING' AND chapter_uuid IN (SELECT chapter_uuid FROM chapters
                    WHERE review_phase='SYNTHESIZING' AND approved_run_generation=run_generation AND approved_review_revision=review_revision)""")
                await conn.execute("""UPDATE chapters SET review_phase='SYNTHESIZING',status='STREAMING',master_version=master_version+1,
                    state_revision=state_revision+1 WHERE review_phase='MASTERING' AND approved_run_generation=run_generation
                    AND approved_review_revision=review_revision AND approved_at IS NOT NULL""")
                cursor = await conn.execute("SELECT chapter_uuid FROM chapters WHERE review_phase='SYNTHESIZING'")
                chapters = [r[0] for r in await cursor.fetchall()]
                await conn.commit()
        for chapter_uuid in chapters:
            await self.schedule_approved_chapter(chapter_uuid)


