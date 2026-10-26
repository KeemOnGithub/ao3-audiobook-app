"""Database repository operations for SQLite WAL mode per Spec 04."""

import json
import uuid
from typing import List, Optional, Union
import aiosqlite

from backend.app.db.connection import DatabaseManager
from backend.app.db.review_repository import ReviewRepository
from backend.app.db.models import (
    ChapterDTO,
    CharacterDTO,
    LibraryChapterDTO,
    MasterClaim,
    SegmentClaim,
    SegmentDTO,
    SegmentResult,
    WorkDTO,
)

DEFAULT_NARRATOR_INSTRUCT = (
    "A warm, articulate, neutral-accented audiobook narrator with steady pacing, "
    "clear studio diction, and calm storytelling resonance."
)
DEFAULT_NARRATOR_REF = (
    "The evening shadows lengthened across the quiet room as the story began to unfold."
)


class AudiobookRepository(ReviewRepository):
    """Encapsulates all database queries and atomic transactions."""

    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager

    async def upsert_work(self, work_id: str, title: str, author: str, fandoms: List[str]) -> WorkDTO:
        async with self.db_manager.write_lock:
            async with self.db_manager.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO works (work_id, title, author, fandoms_json)
                    VALUES (:work_id, :title, :author, :fandoms_json)
                    ON CONFLICT(work_id) DO UPDATE SET
                        title = excluded.title,
                        author = excluded.author,
                        fandoms_json = excluded.fandoms_json,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now');
                    """,
                    {
                        "work_id": work_id,
                        "title": title,
                        "author": author,
                        "fandoms_json": json.dumps(fandoms),
                    },
                )
                await conn.commit()

        # Ensure default narrator exists for this work
        await self.ensure_default_narrator(work_id)
        return WorkDTO(work_id=work_id, title=title, author=author, fandoms=fandoms)

    async def get_work_by_id(self, work_id: str) -> Optional[WorkDTO]:
        async with self.db_manager.get_connection() as conn:
            cursor = await conn.execute(
                "SELECT work_id, title, author, fandoms_json FROM works WHERE work_id = ?;",
                (work_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return WorkDTO(
                work_id=row["work_id"],
                title=row["title"],
                author=row["author"],
                fandoms=json.loads(row["fandoms_json"] or "[]"),
            )

    async def ensure_default_narrator(self, work_id: str, voice_scope: str = "") -> CharacterDTO:
        async with self.db_manager.write_lock:
            async with self.db_manager.get_connection() as conn:
                cursor = await conn.execute(
                    "SELECT * FROM characters WHERE work_id = ? AND voice_scope = ? AND character_key = 'char_narrator';",
                    (work_id, voice_scope),
                )
                row = await cursor.fetchone()
                if row:
                    return self._row_to_character(row)

                char_uuid = str(uuid.uuid4())
                await conn.execute(
                    """
                    INSERT INTO characters (
                        character_uuid, work_id, voice_scope, character_key, canonical_name,
                        is_narrator, aliases_json, voice_instruct, reference_text,
                        voice_status, voice_version
                    ) VALUES (
                        :character_uuid, :work_id, :voice_scope, 'char_narrator', 'Narrator',
                        1, '[]', :voice_instruct, :reference_text,
                        'PENDING', 1
                    );
                    """,
                    {
                        "character_uuid": char_uuid,
                        "work_id": work_id,
                        "voice_scope": voice_scope,
                        "voice_instruct": DEFAULT_NARRATOR_INSTRUCT,
                        "reference_text": DEFAULT_NARRATOR_REF,
                    },
                )
                await conn.commit()

        return await self.get_character_by_uuid(char_uuid)

    async def upsert_chapter(
        self,
        work_id: str,
        chapter_id: str,
        chapter_number: int,
        chapter_title: str,
        source_url: str,
        total_chunks: int = 0,
        voice_scope: str = "",
    ) -> ChapterDTO:
        voice_scope = voice_scope or str(uuid.uuid4())
        async with self.db_manager.write_lock:
            async with self.db_manager.get_connection() as conn:
                cursor = await conn.execute(
                    "SELECT chapter_uuid FROM chapters WHERE work_id = ? AND chapter_id = ?;",
                    (work_id, chapter_id),
                )
                row = await cursor.fetchone()
                if row:
                    chapter_uuid = row["chapter_uuid"]
                    await conn.execute(
                        """
                        UPDATE chapters SET
                            chapter_number = :chapter_number,
                            chapter_title = :chapter_title,
                            source_url = :source_url,
                            total_chunks = :total_chunks,
                            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                        WHERE chapter_uuid = :chapter_uuid;
                        """,
                        {
                            "chapter_uuid": chapter_uuid,
                            "chapter_number": chapter_number,
                            "chapter_title": chapter_title,
                            "source_url": source_url,
                            "total_chunks": total_chunks,
                        },
                    )
                else:
                    chapter_uuid = str(uuid.uuid4())
                    await conn.execute(
                        """
                        INSERT INTO chapters (
                            chapter_uuid, work_id, voice_scope, chapter_id, chapter_number,
                            chapter_title, source_url, status, total_chunks
                        ) VALUES (
                            :chapter_uuid, :work_id, :voice_scope, :chapter_id, :chapter_number,
                            :chapter_title, :source_url, 'STREAMING', :total_chunks
                        );
                        """,
                        {
                            "chapter_uuid": chapter_uuid,
                            "work_id": work_id,
                            "voice_scope": voice_scope,
                            "chapter_id": chapter_id,
                            "chapter_number": chapter_number,
                            "chapter_title": chapter_title,
                            "source_url": source_url,
                            "total_chunks": total_chunks,
                        },
                    )
                await conn.commit()

        return await self.get_chapter_by_uuid(chapter_uuid)

    async def get_saved_chapter_library(self) -> List[LibraryChapterDTO]:
        """Read persisted chapter metadata without loading script or audio payloads."""
        async with self.db_manager.get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT w.work_id, w.title, w.author,
                       w.created_at AS work_created_at, w.updated_at AS work_updated_at,
                       c.chapter_uuid, c.chapter_id, c.chapter_number, c.chapter_title,
                       c.source_url, c.status, c.master_mp3_path, c.created_at, c.updated_at,c.review_phase,
                       (c.approved_at IS NOT NULL AND c.analysis_completed_at IS NOT NULL AND c.approved_run_generation=c.run_generation
                        AND c.approved_review_revision=c.review_revision AND c.review_phase IN ('SYNTHESIZING','MASTERING','COMPLETED')) AS narration_authorized
                FROM chapters c JOIN works w ON w.work_id = c.work_id
                ORDER BY w.title, w.work_id, c.chapter_number, c.chapter_uuid;
                """
            )
            return [LibraryChapterDTO(**dict(row)) for row in await cursor.fetchall()]

    async def get_chapter_by_uuid(self, chapter_uuid: str) -> Optional[ChapterDTO]:
        async with self.db_manager.get_connection() as conn:
            cursor = await conn.execute("SELECT * FROM chapters WHERE chapter_uuid = ?;", (chapter_uuid,))
            row = await cursor.fetchone()
            if not row:
                return None
            return self._row_to_chapter(row)

    async def get_chapter_by_work_and_id(self, work_id: str, chapter_id: str) -> Optional[ChapterDTO]:
        async with self.db_manager.get_connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM chapters WHERE work_id = ? AND chapter_id = ?;", (work_id, chapter_id)
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return self._row_to_chapter(row)

    async def get_characters_for_work(self, work_id: str, voice_scope: Optional[str] = None) -> List[CharacterDTO]:
        async with self.db_manager.get_connection() as conn:
            scope_filter = (
                " AND voice_scope = :voice_scope"
                if voice_scope is not None
                else " AND voice_scope = COALESCE(("
                "SELECT voice_scope FROM chapters WHERE work_id = :work_id "
                "ORDER BY updated_at DESC LIMIT 1), '')"
            )
            cursor = await conn.execute(
                f"SELECT * FROM characters WHERE work_id = :work_id{scope_filter} ORDER BY is_narrator DESC, canonical_name ASC;",
                {"work_id": work_id, "voice_scope": voice_scope},
            )
            rows = await cursor.fetchall()
            return [self._row_to_character(r) for r in rows]

    async def get_characters_for_chapter(self, chapter_uuid: str) -> List[CharacterDTO]:
        async with self.db_manager.get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT c.* FROM characters c
                JOIN chapters ch ON ch.work_id = c.work_id AND ch.voice_scope = c.voice_scope
                WHERE ch.chapter_uuid = ?
                ORDER BY c.is_narrator DESC, c.canonical_name ASC;
                """,
                (chapter_uuid,),
            )
            return [self._row_to_character(row) for row in await cursor.fetchall()]

    async def get_character_by_key(self, work_id: str, character_key: str, voice_scope: str = "") -> Optional[CharacterDTO]:
        async with self.db_manager.get_connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM characters WHERE work_id = ? AND character_key = ? AND voice_scope = ?;",
                (work_id, character_key, voice_scope),
            )
            row = await cursor.fetchone()
            return self._row_to_character(row) if row else None

    async def get_character_by_uuid(self, character_uuid: str) -> Optional[CharacterDTO]:
        async with self.db_manager.get_connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM characters WHERE character_uuid = ?;",
                (character_uuid,),
            )
            row = await cursor.fetchone()
            return self._row_to_character(row) if row else None

    async def insert_character(
        self,
        work_id: str,
        character_key: str,
        canonical_name: str,
        aliases: List[str],
        voice_instruct: str,
        reference_text: str,
        voice_status: str = "PENDING",
        embedding_pt_path: Optional[str] = None,
        preview_mp3_path: Optional[str] = None,
        voice_version: int = 1,
        voice_scope: str = "",
    ) -> CharacterDTO:
        char_uuid = str(uuid.uuid4())
        async with self.db_manager.write_lock:
            async with self.db_manager.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO characters (
                        character_uuid, work_id, voice_scope, character_key, canonical_name,
                        aliases_json, voice_instruct, reference_text, voice_status,
                        embedding_pt_path, preview_mp3_path, voice_version
                    ) VALUES (
                        :character_uuid, :work_id, :voice_scope, :character_key, :canonical_name,
                        :aliases_json, :voice_instruct, :reference_text, :voice_status,
                        :embedding_pt_path, :preview_mp3_path, :voice_version
                    );
                    """,
                    {
                        "character_uuid": char_uuid,
                        "work_id": work_id,
                        "voice_scope": voice_scope,
                        "character_key": character_key,
                        "canonical_name": canonical_name,
                        "aliases_json": json.dumps(aliases),
                        "voice_instruct": voice_instruct,
                        "reference_text": reference_text,
                        "voice_status": voice_status,
                        "embedding_pt_path": embedding_pt_path,
                        "preview_mp3_path": preview_mp3_path,
                        "voice_version": voice_version,
                    },
                )
                await conn.commit()
        return await self.get_character_by_uuid(char_uuid)

    async def update_character_voice(
        self,
        character_uuid: str,
        voice_instruct: str,
        voice_status: str,
        voice_version: Optional[int] = None,
        embedding_pt_path: Optional[str] = None,
        preview_mp3_path: Optional[str] = None,
    ) -> None:
        async with self.db_manager.write_lock:
            async with self.db_manager.get_connection() as conn:
                fields = ["voice_instruct = :voice_instruct", "voice_status = :voice_status"]
                params = {
                    "character_uuid": character_uuid,
                    "voice_instruct": voice_instruct,
                    "voice_status": voice_status,
                }
                if voice_version is not None:
                    fields.append("voice_version = :voice_version")
                    params["voice_version"] = voice_version
                if embedding_pt_path is not None:
                    fields.append("embedding_pt_path = :embedding_pt_path")
                    params["embedding_pt_path"] = embedding_pt_path
                if preview_mp3_path is not None:
                    fields.append("preview_mp3_path = :preview_mp3_path")
                    params["preview_mp3_path"] = preview_mp3_path

                fields.append("updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')")

                sql = f"UPDATE characters SET {', '.join(fields)} WHERE character_uuid = :character_uuid;"
                await conn.execute(sql, params)
                await conn.commit()

    async def bulk_insert_segments(self, segments: List[dict], expected_generation: int = 0,
                                   completed_chunk: Optional[int] = None) -> List[SegmentDTO]:
        inserted_uuids: List[str] = []
        async with self.db_manager.write_lock:
            async with self.db_manager.get_connection() as conn:
                for chapter_uuid in {s['chapter_uuid'] for s in segments}:
                    cursor = await conn.execute('SELECT run_generation,review_phase FROM chapters WHERE chapter_uuid=?', (chapter_uuid,))
                    chapter = await cursor.fetchone()
                    if not chapter or chapter['review_phase'] != 'ANALYZING' or chapter['run_generation'] != expected_generation:
                        return []
                for seg in segments:
                    seg_uuid = seg.get("segment_uuid") or str(uuid.uuid4())
                    seg["segment_uuid"] = seg_uuid
                    inserted_uuids.append(seg_uuid)
                    await conn.execute(
                        """
                        INSERT INTO segments (
                            segment_uuid, chapter_uuid, chunk_index, segment_order,
                            paragraph_index, source_continuation,
                            character_uuid, text_content, is_paragraph_end, status,
                            voice_version_used
                        ) VALUES (
                            :segment_uuid, :chapter_uuid, :chunk_index, :segment_order,
                            :paragraph_index, :source_continuation,
                            :character_uuid, :text_content, :is_paragraph_end, 'QUEUED',
                            :voice_version_used
                        ) ON CONFLICT(chapter_uuid, segment_order) DO UPDATE SET
                            text_content = excluded.text_content,
                            paragraph_index = excluded.paragraph_index,
                            source_continuation = excluded.source_continuation,
                            character_uuid = excluded.character_uuid,
                            is_paragraph_end = excluded.is_paragraph_end,
                            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now');
                        """,
                        {
                            "segment_uuid": seg_uuid,
                            "chapter_uuid": seg["chapter_uuid"],
                            "chunk_index": seg["chunk_index"],
                            "segment_order": seg["segment_order"],
                            "paragraph_index": seg.get("paragraph_index"),
                            "source_continuation": 1 if seg.get("source_continuation") else 0,
                            "character_uuid": seg["character_uuid"],
                            "text_content": seg["text_content"],
                            "is_paragraph_end": 1 if seg.get("is_paragraph_end") else 0,
                            "voice_version_used": seg.get("voice_version_used", 1),
                        },
                    )
                if completed_chunk is not None and segments:
                    await conn.execute("""UPDATE chapters SET llm_completed_chunks=?,
                        total_segments=(SELECT COUNT(*) FROM segments WHERE chapter_uuid=?),state_revision=state_revision+1
                        WHERE chapter_uuid=? AND llm_completed_chunks=?""",
                        (completed_chunk+1, segments[0]['chapter_uuid'], segments[0]['chapter_uuid'], completed_chunk))
                await conn.commit()

        if not segments:
            return []

        ch_uuid = segments[0]["chapter_uuid"]
        orders = [s["segment_order"] for s in segments]
        placeholders = ",".join("?" for _ in orders)
        async with self.db_manager.get_connection() as conn:
            cursor = await conn.execute(
                f"SELECT * FROM segments WHERE chapter_uuid = ? AND segment_order IN ({placeholders}) ORDER BY segment_order ASC;",
                (ch_uuid, *orders),
            )
            rows = await cursor.fetchall()
            return [self._row_to_segment(r) for r in rows if r]

    async def get_segment_by_uuid(self, segment_uuid: str) -> Optional[SegmentDTO]:
        async with self.db_manager.get_connection() as conn:
            cursor = await conn.execute("SELECT * FROM segments WHERE segment_uuid = ?;", (segment_uuid,))
            row = await cursor.fetchone()
            return self._row_to_segment(row) if row else None

    async def get_segments_for_chapter(self, chapter_uuid: str) -> List[SegmentDTO]:
        async with self.db_manager.get_connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM segments WHERE chapter_uuid = ? ORDER BY segment_order ASC;",
                (chapter_uuid,),
            )
            rows = await cursor.fetchall()
            return [self._row_to_segment(r) for r in rows]

    async def update_segment_ready(
        self,
        segment_uuid: str,
        character_uuid: str,
        target_voice_version: int,
        audio_path: str,
        duration_ms: int,
    ) -> bool:
        """Unclaimed publication is no longer supported."""
        return False

    async def try_claim_mastering_gate(self, chapter_uuid: str) -> tuple[bool, int]:
        """Compatibility delegate; never an alternate authorization gate."""
        chapter = await self.get_chapter_by_uuid(chapter_uuid)
        claim = await self.claim_mastering(chapter_uuid,chapter.run_generation) if chapter else None
        return claim is not None,claim.master_version if claim else 0

    async def supersede_active_chapters(self, except_chapter_uuid: str = "") -> List[str]:
        """Fence abandoned runs without deleting playable audio or scripted text."""
        async with self.db_manager.write_lock:
            async with self.db_manager.get_connection() as conn:
                cursor = await conn.execute(
                    "SELECT chapter_uuid FROM chapters WHERE review_phase IN ('ANALYZING','SYNTHESIZING','MASTERING') AND status!='COMPLETED' AND chapter_uuid != ?;",
                    (except_chapter_uuid,),
                )
                chapter_ids = [row["chapter_uuid"] for row in await cursor.fetchall()]
                for chapter_uuid in chapter_ids:
                    await conn.execute(
                        """UPDATE chapters SET status = 'FAILED',review_phase='FAILED', run_generation = run_generation + 1,
                        approved_run_generation=NULL,approved_review_revision=NULL,approved_at=NULL,
                        state_revision = state_revision + 1,
                        error_message = '[SUPERSEDED] Stopped because a new chapter was loaded. Retry or regenerate to resume.',
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE chapter_uuid = ?;""",
                        (chapter_uuid,),
                    )
                    await conn.execute("UPDATE voice_preview_candidates SET status='REVOKED' WHERE chapter_uuid=?", (chapter_uuid,))
                    await conn.execute(
                        """UPDATE segments SET status = 'FAILED', claimed_by = NULL,
                        claimed_generation = NULL, claimed_at = NULL,
                        last_error_code = 'SUPERSEDED', error_message = 'Stopped because a new chapter was loaded.'
                        WHERE chapter_uuid = ? AND status IN ('QUEUED', 'SYNTHESIZING');""",
                        (chapter_uuid,),
                    )
                await conn.commit()
                return chapter_ids

    async def claim_segment(
        self,
        segment_uuid: str,
        run_generation: int,
        target_voice_version: int,
        worker_token: str,
        character_uuid: str = "",
        approved_review_revision: int = -1,
    ) -> Optional[SegmentClaim]:
        """Atomically claim a QUEUED segment for processing in the current generation."""
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        async with self.db_manager.write_lock:
            async with self.db_manager.get_connection() as conn:
                cursor = await conn.execute(
                    """
                    UPDATE segments
                    SET
                        status = 'SYNTHESIZING',
                        claimed_by = :worker_token,
                        claimed_generation = :run_generation,
                        claimed_at = :claimed_at,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE segment_uuid = :segment_uuid
                      AND character_uuid = :character_uuid
                      AND status = 'QUEUED'
                      AND (
                          SELECT ch.run_generation
                           FROM chapters ch
                           WHERE ch.chapter_uuid = segments.chapter_uuid
                              AND ch.review_phase = 'SYNTHESIZING'
                              AND ch.analysis_completed_at IS NOT NULL AND ch.approved_at IS NOT NULL
                              AND ch.approved_run_generation = ch.run_generation
                              AND ch.approved_review_revision = ch.review_revision
                              AND ch.review_revision = :approved_review_revision
                       ) = :run_generation
                      AND (
                          SELECT c.voice_version
                          FROM characters c
                          WHERE c.character_uuid = segments.character_uuid
                      ) = :target_voice_version;
                    """,
                    {
                        "segment_uuid": segment_uuid,
                        "worker_token": worker_token,
                        "run_generation": run_generation,
                        "claimed_at": now,
                        "target_voice_version": target_voice_version,
                        "character_uuid": character_uuid,
                        "approved_review_revision": approved_review_revision,
                    },
                )
                if cursor.rowcount == 1:
                    await conn.commit()
                    return SegmentClaim(
                        segment_uuid=segment_uuid,
                        run_generation=run_generation,
                        target_voice_version=target_voice_version,
                        worker_token=worker_token,
                        claimed_at=now,
                        character_uuid=character_uuid,
                        approved_review_revision=approved_review_revision,
                    )
                return None

    async def record_segment_retry_or_failure(
        self,
        segment_uuid: str,
        claim: SegmentClaim,
        error_code: str,
        message: str,
        retryable: bool,
        max_attempts: int = 3,
    ) -> SegmentResult:
        """Record segment attempt failure, increment retry count, and transition to QUEUED or FAILED."""
        async with self.db_manager.write_lock:
            async with self.db_manager.get_connection() as conn:
                cursor = await conn.execute(
                    "SELECT retry_count, chapter_uuid FROM segments WHERE segment_uuid = ?;",
                    (segment_uuid,),
                )
                row = await cursor.fetchone()
                if not row:
                    return SegmentResult(
                        segment_uuid=segment_uuid,
                        status="FAILED",
                        retry_count=0,
                        retryable=False,
                        error_code=error_code,
                        error_message=message,
                    )
                current_retries = row["retry_count"]
                ch_uuid = row["chapter_uuid"]
                new_retry_count = current_retries + 1

                should_retry = retryable and (new_retry_count < max_attempts)
                next_status = "QUEUED" if should_retry else "FAILED"

                cursor = await conn.execute(
                    """
                    UPDATE segments
                    SET
                        status = :next_status,
                        retry_count = :new_retry_count,
                        last_error_code = :error_code,
                        error_message = :message,
                        claimed_by = NULL,
                        claimed_generation = NULL,
                        claimed_at = NULL,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE segment_uuid = :segment_uuid
                      AND claimed_by = :worker_token
                      AND claimed_generation = :claimed_gen
                      AND character_uuid = :character_uuid
                      AND EXISTS (SELECT 1 FROM chapters ch WHERE ch.chapter_uuid=segments.chapter_uuid
                        AND ch.run_generation=:claimed_gen AND ch.review_phase='SYNTHESIZING'
                        AND ch.approved_at IS NOT NULL AND ch.analysis_completed_at IS NOT NULL
                        AND ch.approved_run_generation=ch.run_generation
                        AND ch.approved_review_revision=ch.review_revision AND ch.review_revision=:review_revision);
                    """,
                    {
                        "next_status": next_status,
                        "new_retry_count": new_retry_count,
                        "error_code": error_code,
                        "message": message,
                        "segment_uuid": segment_uuid,
                        "worker_token": claim.worker_token,
                        "claimed_gen": claim.run_generation,
                        "character_uuid": claim.character_uuid,
                        "review_revision": claim.approved_review_revision,
                    },
                )
                await conn.commit()
                if cursor.rowcount != 1:
                    return SegmentResult(
                        segment_uuid=segment_uuid, status="FAILED", retry_count=current_retries,
                        retryable=False, error_code="STALE_CLAIM", error_message="Claim was superseded.",
                    )

        # This transition acquires write_lock itself and fences the generation.
        if next_status == "FAILED":
            await self.transition_chapter_failed(
                chapter_uuid=ch_uuid,
                run_generation=claim.run_generation,
                code=error_code,
                message=f"Segment {segment_uuid} failed permanently: {message}",
            )

        return SegmentResult(
            segment_uuid=segment_uuid,
            status=next_status,
            retry_count=new_retry_count,
            retryable=should_retry,
            error_code=error_code,
            error_message=message,
        )

    async def commit_segment_ready(
        self,
        segment_uuid: str,
        claim: SegmentClaim,
        audio_path: str,
        duration_ms: int,
    ) -> bool:
        """Atomically commit segment to READY matching its claim and active generation."""
        async with self.db_manager.write_lock:
            async with self.db_manager.get_connection() as conn:
                cursor = await conn.execute(
                    """
                    UPDATE segments
                    SET
                        status = 'READY',
                        audio_path = :audio_path,
                        duration_ms = :duration_ms,
                        voice_version_used = :target_voice_version,
                        last_error_code = NULL,
                        error_message = NULL,
                        claimed_by = NULL,
                        claimed_generation = NULL,
                        claimed_at = NULL,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE segment_uuid = :segment_uuid
                      AND claimed_by = :worker_token
                      AND claimed_generation = :claimed_gen
                      AND character_uuid = :character_uuid
                      AND (
                          SELECT ch.run_generation
                          FROM chapters ch
                           WHERE ch.chapter_uuid = segments.chapter_uuid
                             AND ch.review_phase='SYNTHESIZING' AND ch.approved_at IS NOT NULL
                             AND ch.analysis_completed_at IS NOT NULL
                             AND ch.approved_run_generation=ch.run_generation
                             AND ch.approved_review_revision=ch.review_revision
                             AND ch.review_revision=:review_revision
                      ) = :claimed_gen
                      AND (
                          SELECT c.voice_version
                          FROM characters c
                          WHERE c.character_uuid = segments.character_uuid
                      ) = :target_voice_version;
                    """,
                    {
                        "segment_uuid": segment_uuid,
                        "worker_token": claim.worker_token,
                        "claimed_gen": claim.run_generation,
                        "target_voice_version": claim.target_voice_version,
                        "audio_path": audio_path,
                        "duration_ms": duration_ms,
                        "character_uuid": claim.character_uuid,
                        "review_revision": claim.approved_review_revision,
                    },
                )
                committed = (cursor.rowcount == 1)

                if committed:
                    cursor = await conn.execute(
                        "SELECT chapter_uuid FROM segments WHERE segment_uuid = ?;", (segment_uuid,)
                    )
                    row = await cursor.fetchone()
                    if row:
                        ch_uuid = row["chapter_uuid"]
                        await conn.execute(
                            """
                            UPDATE chapters
                            SET
                                completed_segments = (
                                    SELECT COUNT(*)
                                    FROM segments s
                                    JOIN characters c ON s.character_uuid = c.character_uuid
                                    WHERE s.chapter_uuid = :chapter_uuid
                                      AND s.status = 'READY'
                                      AND s.voice_version_used = c.voice_version
                                ),
                                state_revision = state_revision + 1,
                                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                            WHERE chapter_uuid = :chapter_uuid;
                            """,
                            {"chapter_uuid": ch_uuid},
                        )

                await conn.commit()
                return committed

    async def transition_chapter_failed(
        self,
        chapter_uuid: str,
        run_generation: int,
        code: str,
        message: str,
    ) -> bool:
        """Atomically transition chapter to FAILED if in current generation."""
        async with self.db_manager.write_lock:
            async with self.db_manager.get_connection() as conn:
                cursor = await conn.execute(
                    """
                    UPDATE chapters
                    SET
                        status = 'FAILED',
                        review_phase = 'FAILED',
                        error_message = :err,
                        state_revision = state_revision + 1,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE chapter_uuid = :chapter_uuid
                      AND run_generation = :run_gen
                      AND status != 'FAILED';
                    """,
                    {
                        "chapter_uuid": chapter_uuid,
                        "run_gen": run_generation,
                        "err": f"[{code}] {message}" if code else message,
                    },
                )
                committed = (cursor.rowcount == 1)
                await conn.commit()
                return committed

    async def claim_mastering(self, chapter_uuid: str, run_generation: int) -> Optional[MasterClaim]:
        """Atomically claim mastering gate from STREAMING -> MASTERING for current generation."""
        async with self.db_manager.write_lock:
            async with self.db_manager.get_connection() as conn:
                cursor = await conn.execute(
                    """
                    UPDATE chapters
                    SET
                        status = 'MASTERING',
                        review_phase = 'MASTERING',
                        master_version = master_version + 1,
                        state_revision = state_revision + 1,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE chapter_uuid = :chapter_uuid
                      AND run_generation = :run_generation
                      AND status = 'STREAMING'
                      AND review_phase='SYNTHESIZING' AND analysis_completed_at IS NOT NULL
                      AND approved_at IS NOT NULL AND approved_run_generation=run_generation
                      AND approved_review_revision=review_revision
                      AND total_chunks > 0
                      AND llm_completed_chunks >= total_chunks
                      AND total_segments > 0
                      AND NOT EXISTS (
                          SELECT 1
                          FROM segments s
                          JOIN characters c ON s.character_uuid = c.character_uuid
                          WHERE s.chapter_uuid = :chapter_uuid
                            AND (
                                s.status != 'READY'
                                OR s.voice_version_used != c.voice_version
                            )
                      );
                    """,
                    {"chapter_uuid": chapter_uuid, "run_generation": run_generation},
                )
                if cursor.rowcount == 1:
                    c = await conn.execute(
                        "SELECT master_version,approved_review_revision FROM chapters WHERE chapter_uuid = ?;", (chapter_uuid,)
                    )
                    r = await c.fetchone()
                    master_ver = r["master_version"] if r else 1
                    await conn.commit()
                    return MasterClaim(
                        chapter_uuid=chapter_uuid,
                        run_generation=run_generation,
                        master_version=master_ver,
                        approved_review_revision=r['approved_review_revision'],
                    )
                return None

    async def commit_master_completed(
        self,
        chapter_uuid: str,
        claim: Optional[Union[MasterClaim, int]] = None,
        master_mp3_path: str = "",
        expected_master_version: Optional[int] = None,
    ) -> bool:
        """Atomically transition chapter to COMPLETED upon master file validation."""
        if not isinstance(claim, MasterClaim):
            return False
        if expected_master_version is not None and claim is None:
            expected_ver = expected_master_version
            expected_generation = None
        elif isinstance(claim, int):
            expected_ver = claim
            expected_generation = None
        elif isinstance(claim, MasterClaim):
            expected_ver = claim.master_version
            expected_generation = claim.run_generation
        else:
            expected_ver = expected_master_version or 1
            expected_generation = None

        async with self.db_manager.write_lock:
            async with self.db_manager.get_connection() as conn:
                where_clauses = [
                    "chapter_uuid = :chapter_uuid",
                    "status = 'MASTERING'",
                    "master_version = :expected_master_version",
                    "review_phase = 'MASTERING'",
                    "analysis_completed_at IS NOT NULL AND approved_at IS NOT NULL",
                    "approved_run_generation=run_generation",
                    "approved_review_revision=review_revision AND review_revision=:review_revision",
                ]
                params = {
                    "chapter_uuid": chapter_uuid,
                    "master_mp3_path": master_mp3_path,
                    "expected_master_version": expected_ver,
                    "review_revision": claim.approved_review_revision,
                }
                if expected_generation is not None:
                    where_clauses.append("run_generation = :run_generation")
                    params["run_generation"] = expected_generation

                sql = f"""
                UPDATE chapters
                SET
                    status = 'COMPLETED',
                    review_phase = 'COMPLETED',
                    master_mp3_path = :master_mp3_path,
                    state_revision = state_revision + 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE {' AND '.join(where_clauses)};
                """
                cursor = await conn.execute(sql, params)
                committed = (cursor.rowcount == 1)
                await conn.commit()
                return committed

    async def fail_mastering(
        self, chapter_uuid: str, claim: MasterClaim, code: str, message: str
    ) -> bool:
        """Transition chapter to FAILED on mastering failure."""
        async with self.db_manager.write_lock:
            async with self.db_manager.get_connection() as conn:
                cursor = await conn.execute("""UPDATE chapters SET status='FAILED',review_phase='FAILED',
                    error_message=?,state_revision=state_revision+1 WHERE chapter_uuid=? AND run_generation=?
                    AND master_version=? AND review_phase='MASTERING' AND approved_run_generation=run_generation
                    AND approved_review_revision=review_revision AND review_revision=?""",
                    (f'[{code}] {message}',chapter_uuid,claim.run_generation,claim.master_version,claim.approved_review_revision))
                await conn.commit()
                return cursor.rowcount == 1

    async def replace_chapter_generation(
        self,
        chapter_uuid: str,
        new_generation: int,
        total_chunks: int = 0,
        mode: str = "FULL",
    ) -> Optional[ChapterDTO]:
        """Atomically advance chapter run_generation and reset state."""
        if mode == 'AUDIO_ONLY':
            chapter = await self.get_chapter_by_uuid(chapter_uuid)
            if chapter:
                await self.return_chapter_to_review(chapter_uuid, chapter.run_generation, chapter.review_revision)
            return await self.get_chapter_by_uuid(chapter_uuid)
        async with self.db_manager.write_lock:
            async with self.db_manager.get_connection() as conn:
                if mode == "FULL":
                    await conn.execute(
                        "DELETE FROM segments WHERE chapter_uuid = :ch_uuid;",
                        {"ch_uuid": chapter_uuid},
                    )
                    await conn.execute(
                        """
                        UPDATE chapters
                        SET
                            run_generation = :new_gen,
                            voice_scope = :voice_scope,
                            state_revision = state_revision + 1,
                            status = 'STREAMING',
                            review_phase = 'ANALYZING',review_revision=0,analysis_completed_at=NULL,
                            approved_run_generation=NULL,approved_review_revision=NULL,approved_at=NULL,
                            total_chunks = :total_chunks,
                            llm_completed_chunks = 0,
                            total_segments = 0,
                            completed_segments = 0,
                            master_mp3_path = NULL,
                            error_message = NULL,
                            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                        WHERE chapter_uuid = :ch_uuid;
                        """,
                        {"ch_uuid": chapter_uuid, "new_gen": new_generation, "total_chunks": total_chunks,
                         "voice_scope": str(uuid.uuid4())},
                    )
                else:  # AUDIO_ONLY
                    await conn.execute(
                        """
                        UPDATE segments
                        SET
                            status = 'QUEUED',
                            audio_path = NULL,
                            retry_count = 0,
                            claimed_by = NULL,
                            claimed_generation = NULL,
                            claimed_at = NULL,
                            last_error_code = NULL,
                            error_message = NULL,
                            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                        WHERE chapter_uuid = :ch_uuid;
                        """,
                        {"ch_uuid": chapter_uuid},
                    )
                    await conn.execute(
                        """
                        UPDATE chapters
                        SET
                            run_generation = :new_gen,
                            state_revision = state_revision + 1,
                            status = 'STREAMING',
                            completed_segments = 0,
                            master_mp3_path = NULL,
                            error_message = NULL,
                            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                        WHERE chapter_uuid = :ch_uuid;
                        """,
                        {"ch_uuid": chapter_uuid, "new_gen": new_generation},
                    )
                await conn.execute("UPDATE voice_preview_candidates SET status='REVOKED' WHERE chapter_uuid=?", (chapter_uuid,))
                await conn.commit()
        return await self.get_chapter_by_uuid(chapter_uuid)

    @staticmethod
    def _row_to_chapter(row: aiosqlite.Row) -> ChapterDTO:
        keys = set(row.keys())
        return ChapterDTO(
            chapter_uuid=row["chapter_uuid"],
            voice_scope=row["voice_scope"],
            work_id=row["work_id"],
            chapter_id=row["chapter_id"],
            chapter_number=row["chapter_number"],
            chapter_title=row["chapter_title"],
            source_url=row["source_url"],
            status=row["status"],
            **{name: row[name] for name in ('review_phase','review_revision','analysis_completed_at',
                'approved_run_generation','approved_review_revision','approved_at')},
            run_generation=row["run_generation"] if "run_generation" in keys else 0,
            state_revision=row["state_revision"] if "state_revision" in keys else 0,
            total_chunks=row["total_chunks"],
            llm_completed_chunks=row["llm_completed_chunks"],
            total_segments=row["total_segments"],
            completed_segments=row["completed_segments"],
            master_mp3_path=row["master_mp3_path"],
            master_version=row["master_version"],
            error_message=row["error_message"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_character(row: aiosqlite.Row) -> CharacterDTO:
        keys = set(row.keys())
        aliases = []
        if row["aliases_json"]:
            try:
                aliases = json.loads(row["aliases_json"])
            except Exception:
                aliases = []
        return CharacterDTO(
            character_uuid=row["character_uuid"],
            voice_scope=row["voice_scope"],
            work_id=row["work_id"],
            character_key=row["character_key"],
            canonical_name=row["canonical_name"],
            is_narrator=row["is_narrator"],
            aliases=aliases,
            voice_instruct=row["voice_instruct"],
            reference_text=row["reference_text"],
            voice_status=row["voice_status"],
            embedding_pt_path=row["embedding_pt_path"],
            preview_mp3_path=row["preview_mp3_path"],
            voice_version=row["voice_version"],
            artifact_provider=row["artifact_provider"] if "artifact_provider" in keys else None,
            artifact_fingerprint=row["artifact_fingerprint"] if "artifact_fingerprint" in keys else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_segment(row: aiosqlite.Row) -> SegmentDTO:
        keys = set(row.keys())
        return SegmentDTO(
            segment_uuid=row["segment_uuid"],
            chapter_uuid=row["chapter_uuid"],
            chunk_index=row["chunk_index"],
            segment_order=row["segment_order"],
            character_uuid=row["character_uuid"],
            text_content=row["text_content"],
            paragraph_index=row["paragraph_index"] if "paragraph_index" in keys else None,
            source_continuation=row["source_continuation"] if "source_continuation" in keys else 0,
            delivery_instruct=row["delivery_instruct"],
            is_paragraph_end=row["is_paragraph_end"],
            audio_path=row["audio_path"],
            duration_ms=row["duration_ms"],
            status=row["status"],
            voice_version_used=row["voice_version_used"],
            retry_count=row["retry_count"],
            claimed_by=row["claimed_by"] if "claimed_by" in keys else None,
            claimed_generation=row["claimed_generation"] if "claimed_generation" in keys else None,
            claimed_at=row["claimed_at"] if "claimed_at" in keys else None,
            last_error_code=row["last_error_code"] if "last_error_code" in keys else None,
            error_message=row["error_message"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
