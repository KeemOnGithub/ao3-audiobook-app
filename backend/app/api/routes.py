"""FastAPI REST routes and Server-Sent Events stream per Spec 05."""

import asyncio
import json
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, StreamingResponse

from backend.app.api.audio_stream import serve_byte_range_audio
from backend.app.api.schemas import (
    ChapterLoadRequest,
    ChapterLoadResponse,
    ChapterStateResponse,
    ChapterStatus,
    CharacterSchema,
    CharacterVoiceUpdateRequest,
    LibraryChapterSchema,
    LibraryResponse,
    LibraryWorkSchema,
    MasterDownloadProgressResponse,
    PlaybackSeekRequest,
    RetryFailedRequest,
    SegmentSchema,
    SegmentStatus,
    VoiceUpdateResponse,
    ReviewIdentityRequest,
    VoicePreviewRequest,
    RenameReviewCharacterRequest,AddReviewCharacterRequest,MergeReviewCharactersRequest,ReassignReviewSpeakersRequest,
)
from backend.app.db.repository import AudiobookRepository
from backend.app.db.review_repository import ReviewConflict
from backend.app.ingestion.exceptions import IngestionError, InvalidAO3UrlError
from backend.app.orchestration.orchestrator import JobOrchestrator

router = APIRouter(prefix="/api/v1")


@router.post('/chapters/{chapter_uuid}/review/approve')
async def approve_review(chapter_uuid: str, req: ReviewIdentityRequest, request: Request):
    changed = await get_repo(request).approve_chapter_review(chapter_uuid,req.expected_run_generation,req.expected_review_revision)
    if changed:
        await get_orchestrator(request).cancel_voice_previews(chapter_uuid)
        await get_orchestrator(request).emit_review_changed(chapter_uuid,'approved')
        await get_orchestrator(request).schedule_approved_chapter(chapter_uuid)
    return await get_chapter_state(chapter_uuid,request)


def public_candidate(candidate):
    fields = ('candidate_uuid','chapter_uuid','character_uuid','run_generation','base_voice_version','voice_instruct',
        'reference_text','input_digest','status','duration_ms','artifact_provider','artifact_fingerprint','error_code','error_message')
    result = {k:candidate[k] for k in fields}
    result['preview_url'] = (f"/api/v1/audio/voice-previews/{candidate['candidate_uuid']}.mp3?token={candidate['input_digest']}"
        if candidate['status'] == 'READY' else None)
    return result


async def correction_result(chapter_uuid,request):
    await get_orchestrator(request).emit_review_changed(chapter_uuid,'cast_corrected')
    return await get_chapter_state(chapter_uuid,request)


@router.post('/chapters/{chapter_uuid}/review/characters')
async def add_review_character(chapter_uuid: str,req: AddReviewCharacterRequest,request: Request):
    await get_repo(request).add_review_character(chapter_uuid,req.expected_run_generation,req.expected_review_revision,req.canonical_name,req.voice_instruct)
    return await correction_result(chapter_uuid,request)


@router.patch('/chapters/{chapter_uuid}/review/characters/{character_uuid}')
async def rename_review_character(chapter_uuid: str,character_uuid: str,req: RenameReviewCharacterRequest,request: Request):
    await get_repo(request).rename_review_character(chapter_uuid,character_uuid,req.expected_run_generation,req.expected_review_revision,req.canonical_name)
    return await correction_result(chapter_uuid,request)


@router.delete('/chapters/{chapter_uuid}/review/characters/{character_uuid}')
async def delete_review_character(chapter_uuid: str,character_uuid: str,req: ReviewIdentityRequest,request: Request):
    await get_repo(request).delete_review_character(chapter_uuid,character_uuid,req.expected_run_generation,req.expected_review_revision)
    return await correction_result(chapter_uuid,request)


@router.post('/chapters/{chapter_uuid}/review/characters/merge')
async def merge_review_characters(chapter_uuid: str,req: MergeReviewCharactersRequest,request: Request):
    await get_repo(request).merge_review_characters(chapter_uuid,req.expected_run_generation,req.expected_review_revision,req.source_character_uuid,req.target_character_uuid)
    return await correction_result(chapter_uuid,request)


@router.post('/chapters/{chapter_uuid}/review/speakers')
async def reassign_review_speakers(chapter_uuid: str,req: ReassignReviewSpeakersRequest,request: Request):
    await get_repo(request).reassign_review_speakers(chapter_uuid,req.expected_run_generation,req.expected_review_revision,req.segment_uuids,req.target_character_uuid)
    return await correction_result(chapter_uuid,request)


@router.post('/chapters/{chapter_uuid}/review/characters/{character_uuid}/voice-previews',status_code=202)
async def create_voice_preview(chapter_uuid: str, character_uuid: str, req: VoicePreviewRequest, request: Request):
    candidate = await get_repo(request).create_voice_preview_candidate(chapter_uuid,character_uuid,
        req.expected_run_generation,req.expected_review_revision,req.voice_instruct,req.reference_text)
    get_orchestrator(request).start_voice_preview(candidate)
    return {'candidate_uuid':candidate['candidate_uuid'],'snapshot':await get_chapter_state(chapter_uuid,request)}


@router.get('/chapters/{chapter_uuid}/review/voice-previews/{candidate_uuid}')
async def get_voice_preview(chapter_uuid: str, candidate_uuid: str, request: Request):
    return public_candidate(await get_repo(request).get_voice_preview_candidate(chapter_uuid,candidate_uuid))


@router.post('/chapters/{chapter_uuid}/review/voice-previews/{candidate_uuid}/apply')
async def apply_voice_preview(chapter_uuid: str, candidate_uuid: str, req: ReviewIdentityRequest, request: Request):
    await get_repo(request).apply_voice_preview_candidate(chapter_uuid,candidate_uuid,req.expected_run_generation,req.expected_review_revision)
    await get_orchestrator(request).emit_review_changed(chapter_uuid,'voice_applied')
    return await get_chapter_state(chapter_uuid,request)


@router.get('/audio/voice-previews/{candidate_uuid}.mp3')
async def candidate_audio(candidate_uuid: str, request: Request, token: str = ''):
    repo = get_repo(request)
    async with repo.db_manager.get_connection() as conn:
        cursor = await conn.execute('SELECT chapter_uuid FROM voice_preview_candidates WHERE candidate_uuid=?',(candidate_uuid,))
        row = await cursor.fetchone()
    if not row:
        raise ReviewConflict('Preview not found','NOT_FOUND',404)
    candidate = await repo.get_voice_preview_candidate(row['chapter_uuid'],candidate_uuid)
    chapter = await repo.get_chapter_by_uuid(row['chapter_uuid'])
    if (candidate['status'] != 'READY' or chapter.review_phase != 'AWAITING_APPROVAL'
            or candidate['run_generation'] != chapter.run_generation or candidate['input_digest'] != token):
        raise ReviewConflict('Preview is no longer accessible','STALE_CANDIDATE')
    return await serve_byte_range_audio(candidate['preview_mp3_path'],request)


@router.post('/chapters/{chapter_uuid}/review/reopen')
async def reopen_review(chapter_uuid: str, req: ReviewIdentityRequest, request: Request):
    changed = await get_repo(request).return_chapter_to_review(chapter_uuid,req.expected_run_generation,req.expected_review_revision)
    if changed:
        await get_orchestrator(request).cancel_chapter_execution(chapter_uuid)
        await get_orchestrator(request).emit_review_changed(chapter_uuid,'reopened')
    return await get_chapter_state(chapter_uuid,request)


def get_orchestrator(request: Request) -> JobOrchestrator:
    return request.app.state.orchestrator


def get_repo(request: Request) -> AudiobookRepository:
    return request.app.state.repository


@router.get("/library", response_model=LibraryResponse)
async def get_library(request: Request):
    """List saved chapters without ingestion, regeneration, or exposing storage paths."""
    try:
        rows = await get_repo(request).get_saved_chapter_library()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to retrieve chapter library.",
        ) from exc

    works = {}
    for row in rows:
        if row.work_id not in works:
            works[row.work_id] = LibraryWorkSchema(
                work_id=row.work_id, title=row.title, author=row.author,
                created_at=row.work_created_at, updated_at=row.work_updated_at,
            )
        works[row.work_id].chapters.append(LibraryChapterSchema(
            chapter_uuid=row.chapter_uuid, chapter_id=row.chapter_id,
            chapter_number=row.chapter_number, chapter_title=row.chapter_title,
            source_url=row.source_url, status=ChapterStatus(row.status),
            review_phase=row.review_phase,
            master_audio_available=bool(row.narration_authorized and row.master_mp3_path and os.path.isfile(row.master_mp3_path)),
            created_at=row.created_at, updated_at=row.updated_at,
        ))
    return LibraryResponse(works=list(works.values()))


@router.post("/chapters/load", response_model=ChapterLoadResponse, status_code=status.HTTP_202_ACCEPTED)
@router.post("/chapters/ingest", response_model=ChapterLoadResponse, status_code=status.HTTP_202_ACCEPTED)
async def load_chapter(req: ChapterLoadRequest, request: Request, response: Response):
    """Ingest AO3 URL, initialize chapter records, and trigger streaming pipeline."""
    orch = get_orchestrator(request)
    repo = get_repo(request)

    try:
        chapter = await orch.ingest_chapter(
            url=req.ao3_url,
            force_regenerate=req.force_regenerate,
        )
    except InvalidAO3UrlError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except IngestionError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))

    if chapter.status == "COMPLETED":
        response.status_code = status.HTTP_200_OK

    work = await repo.get_work_by_id(chapter.work_id)
    work_title = work.title if work else chapter.chapter_title
    author = work.author if work else "AO3 Author"

    return ChapterLoadResponse(
        chapter_uuid=chapter.chapter_uuid,
        work_id=chapter.work_id,
        chapter_id=chapter.chapter_id,
        title=work_title,
        work_title=work_title,
        chapter_title=chapter.chapter_title,
        chapter_number=chapter.chapter_number,
        total_chapters=1,
        author=author,
        status=ChapterStatus(chapter.status),
        total_chunks=chapter.total_chunks,
        run_generation=chapter.run_generation,
        state_revision=chapter.state_revision,
    )


@router.get("/chapters/{chapter_uuid}/state", response_model=ChapterStateResponse)
@router.get("/chapters/{chapter_uuid}", response_model=ChapterStateResponse)
async def get_chapter_state(chapter_uuid: str, request: Request):
    """Return full state snapshot for initial render or reconnect hydration."""
    repo = get_repo(request)
    chapter,characters,segments,candidates,blockers = await repo.get_review_snapshot_records(chapter_uuid)
    if not chapter:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Chapter with UUID '{chapter_uuid}' does not exist.",
        )

    char_counts = {}
    for s in segments:
        char_counts[s.character_uuid] = char_counts.get(s.character_uuid, 0) + 1

    char_map = {c.character_uuid: c for c in characters}

    char_schemas = [
        CharacterSchema(
            reference_text=c.reference_text,artifact_provider=c.artifact_provider,artifact_fingerprint=c.artifact_fingerprint,
            character_uuid=c.character_uuid,
            canonical_name=c.canonical_name,
            voice_instruct=c.voice_instruct,
            voice_version=c.voice_version,
            preview_url=(
                f"/api/v1/audio/previews/{c.character_uuid}.mp3?v={c.voice_version}"
                if c.voice_status == "READY" and c.preview_mp3_path and os.path.exists(c.preview_mp3_path)
                else None
            ),
            voice_status=c.voice_status,
            aliases=c.aliases,
            is_narrator=bool(c.is_narrator),
            segment_count=char_counts.get(c.character_uuid, 0),
        )
        for c in characters
    ]

    ready_count = 0
    seg_schemas = []
    for s in segments:
        c = char_map.get(s.character_uuid)
        is_ready = (s.status == "READY" and chapter.approval_valid
            and chapter.review_phase in ('SYNTHESIZING','MASTERING','COMPLETED')
            and c is not None and s.voice_version_used == c.voice_version and bool(s.audio_path))
        if is_ready:
            ready_count += 1

        c = char_map.get(s.character_uuid)
        speaker_name = c.canonical_name if c else "Narrator"
        is_narrator = bool(c.is_narrator) if c else (s.character_uuid == "narrator")
        is_dialogue = not is_narrator

        seg_schemas.append(
            SegmentSchema(
                segment_uuid=s.segment_uuid,
                segment_order=s.segment_order,
                chunk_index=s.chunk_index,
                paragraph_index=s.paragraph_index,
                source_continuation=bool(s.source_continuation),
                character_uuid=s.character_uuid,
                speaker_name=speaker_name,
                is_narrator=is_narrator,
                is_dialogue=is_dialogue,
                text=s.text_content,
                status=SegmentStatus(s.status),
                error_message=s.error_message,
                retry_count=s.retry_count,
                audio_url=f"/api/v1/audio/segments/{s.segment_uuid}.mp3?v={s.voice_version_used}" if is_ready else None,
                duration_ms=s.duration_ms,
                is_paragraph_end=bool(s.is_paragraph_end),
                voice_version=s.voice_version_used,
            )
        )

    master_ready = chapter.review_phase == 'COMPLETED' and chapter.approval_valid and bool(chapter.master_mp3_path)
    master_url = f"/api/v1/chapters/{chapter_uuid}/download" if master_ready else None

    work = await repo.get_work_by_id(chapter.work_id)

    total_segs = len(segments)
    progress_pct = int((ready_count / max(1, total_segs)) * 100) if total_segs > 0 else 0
    if master_ready:
        progress_pct = 100

    return ChapterStateResponse(
        review_phase=chapter.review_phase,review_revision=chapter.review_revision,
        analysis_completed_at=chapter.analysis_completed_at,approved_run_generation=chapter.approved_run_generation,
        approved_review_revision=chapter.approved_review_revision,approved_at=chapter.approved_at,
        narration_authorized=chapter.approval_valid and chapter.review_phase in ('SYNTHESIZING','MASTERING','COMPLETED'),
        can_approve=not blockers,
        approval_blockers=blockers,
        chapter_uuid=chapter_uuid,
        work_id=chapter.work_id,
        work_title=work.title if work else None,
        chapter_title=chapter.chapter_title,
        chapter_number=chapter.chapter_number,
        total_chapters=1,
        author=work.author if work else "AO3 Author",
        source_url=chapter.source_url,
        status=ChapterStatus(chapter.status),
        error_message=chapter.error_message,
        run_generation=chapter.run_generation,
        state_revision=chapter.state_revision,
        total_chunks=chapter.total_chunks,
        scripted_chunks=chapter.llm_completed_chunks,
        master_mp3_ready=master_ready,
        master_mp3_url=master_url,
        master_mp3_progress=progress_pct,
        total_segments=total_segs,
        ready_segments=ready_count,
        characters=char_schemas,
        segments=seg_schemas,
        voice_preview_candidates=[public_candidate(c) for c in candidates],
    )


@router.post("/chapters/{chapter_uuid}/seek")
async def seek_playback(chapter_uuid: str, req: PlaybackSeekRequest, request: Request):
    """Reorder pending tasks in priority queue relative to user seek head."""
    repo = get_repo(request)
    orch = get_orchestrator(request)

    chapter = await repo.get_chapter_by_uuid(chapter_uuid)
    if not chapter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chapter not found")

    if not chapter.approval_valid or chapter.review_phase not in ('SYNTHESIZING','MASTERING','COMPLETED'):
        raise ReviewConflict('Narration requires explicit approval', 'NOT_AUTHORIZED')
    if chapter.review_phase == 'SYNTHESIZING':
        await orch.schedule_approved_chapter(chapter_uuid,master_if_ready=False,playback_order=req.current_playback_order)
    return {'status':'REPRIORITIZED','active_head_order':req.current_playback_order}

@router.post("/chapters/{chapter_uuid}/retry-failed")
async def retry_failed_chapter(chapter_uuid: str, req: RetryFailedRequest, request: Request):
    """Retry failed chapter or failed segments per Spec 07 section 8.4."""
    repo = get_repo(request)
    orch = get_orchestrator(request)

    await repo.retry_approved_chapter(chapter_uuid,req.segment_uuids)
    await orch.schedule_approved_chapter(chapter_uuid)
    await orch.emit_review_changed(chapter_uuid,'retry_approved')
    return await get_chapter_state(chapter_uuid,request)

@router.patch("/characters/{character_uuid}/voice", response_model=VoiceUpdateResponse)
async def update_character_voice(character_uuid: str, req: CharacterVoiceUpdateRequest, request: Request):
    """Old callers must explicitly reopen review and audition a candidate."""
    repo = get_repo(request)
    char = await repo.get_character_by_uuid(character_uuid)
    chapter = await repo.get_chapter_by_uuid(req.current_chapter_uuid)
    if not char or not chapter or char.work_id != chapter.work_id or char.voice_scope != chapter.voice_scope:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Character not found")
    raise ReviewConflict('Return to review and use voice candidates; direct hot swap is no longer supported.', 'REOPEN_REVIEW_REQUIRED')


@router.get("/chapters/{chapter_uuid}/download")
async def download_master_mp3(chapter_uuid: str, request: Request):
    """Download full concatenated master MP3 or return current progress."""
    repo = get_repo(request)
    chapter = await repo.get_chapter_by_uuid(chapter_uuid)
    if not chapter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chapter not found")

    if chapter.review_phase == 'COMPLETED' and chapter.approval_valid and chapter.master_mp3_path and os.path.exists(chapter.master_mp3_path):
        filename = f"{chapter.work_id}_chapter_{chapter.chapter_number}.mp3"
        return FileResponse(
            path=chapter.master_mp3_path,
            media_type="audio/mpeg",
            filename=filename,
        )

    # If still processing, return progress status
    total = max(chapter.total_segments, 1)
    progress_pct = round((chapter.completed_segments / total) * 100.0, 1)
    return Response(
        content=MasterDownloadProgressResponse(
            status=chapter.status,
            progress_pct=progress_pct,
            total_segments=chapter.total_segments,
            processed_segments=chapter.completed_segments,
            estimated_remaining_seconds=max(0, (chapter.total_segments - chapter.completed_segments) * 2),
        ).model_dump_json(),
        status_code=status.HTTP_202_ACCEPTED,
        media_type="application/json",
    )


@router.get("/chapters/{chapter_uuid}/events")
async def sse_event_stream(chapter_uuid: str, request: Request):
    """Server-Sent Events streaming real-time pipeline progress, new characters, and ready audio per Spec 07."""
    orch = get_orchestrator(request)
    repo = get_repo(request)

    chapter = await repo.get_chapter_by_uuid(chapter_uuid)
    if not chapter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chapter not found")

    # Accept Last-Event-ID as optional numeric revision
    last_event_id_header = request.headers.get("Last-Event-ID")
    last_event_id: Optional[int] = None
    if last_event_id_header:
        try:
            last_event_id = int(last_event_id_header.strip())
        except ValueError:
            pass

    subscriber_queue, snapshot_required = await orch.event_bus.subscribe(
        chapter_uuid=chapter_uuid,
        last_event_id=last_event_id,
    )

    async def event_generator():
        # SSE retry handshake per Spec 07
        yield "retry: 2000\n\n"

        if snapshot_required:
            yield "event: snapshot_required\ndata: {}\n\n"

        try:
            # StreamingResponse cancels this generator when the client disconnects.
            while True:
                try:
                    event = await asyncio.wait_for(subscriber_queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                ev_id_str = f"id: {event['id']}\n" if event.get("id") is not None else ""
                yield f"{ev_id_str}event: {event['event']}\ndata: {json.dumps(event['data'])}\n\n"
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            await orch.event_bus.unsubscribe(chapter_uuid, subscriber_queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/audio/segments/{segment_uuid}.mp3")
async def get_segment_audio(segment_uuid: str, request: Request, v: Optional[int] = None):
    """Serve segment audio file with HTTP 206 byte-range streaming."""
    repo = get_repo(request)
    segment = await repo.get_segment_by_uuid(segment_uuid)
    if not segment or not segment.audio_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Segment audio not ready")

    chapter = await repo.get_chapter_by_uuid(segment.chapter_uuid)
    character = await repo.get_character_by_uuid(segment.character_uuid)
    if (not chapter or not chapter.approval_valid or chapter.review_phase not in ('SYNTHESIZING','MASTERING','COMPLETED')
            or segment.status != 'READY' or not character or segment.voice_version_used != character.voice_version
            or (v is not None and v != character.voice_version)):
        raise ReviewConflict('Narration audio is no longer authorized', 'AUDIO_REVOKED')

    return await serve_byte_range_audio(segment.audio_path, request)


@router.get("/audio/previews/{character_uuid}.mp3")
async def get_character_preview(character_uuid: str, request: Request, v: Optional[int] = None):
    """Serve character voice preview clip with HTTP 206 byte-range streaming."""
    repo = get_repo(request)
    char = await repo.get_character_by_uuid(character_uuid)
    if not char or not char.preview_mp3_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Voice preview not ready")

    return await serve_byte_range_audio(char.preview_mp3_path, request)
