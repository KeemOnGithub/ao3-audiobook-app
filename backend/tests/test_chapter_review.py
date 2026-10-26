"""Durable review authorization and migration regression gates."""
import asyncio
import sqlite3
from unittest.mock import AsyncMock
import httpx

import pytest

from backend.app.db.connection import DatabaseManager, SCHEMA_SQL
from backend.app.db.repository import AudiobookRepository
from backend.app.db.review_repository import ReviewConflict
from backend.app.orchestration.event_bus import ChapterEventBus
from backend.app.orchestration.orchestrator import JobOrchestrator
from backend.app.orchestration.queue_models import TTSTask
from backend.app.ingestion.schemas import NarrativeChunk, NormalizedParagraph
from backend.app.scripting.schemas import ChunkAnalysisResult, NewCharacterSpec, ScriptSegmentSpec


async def prepared(tmp_path):
    manager = DatabaseManager(str(tmp_path / 'review.db'))
    await manager.init_db()
    repo = AudiobookRepository(manager)
    await repo.upsert_work('w', 'Title', 'Author', [])
    chapter = await repo.upsert_chapter('w', 'c', 1, 'Chapter', 'url', 1, voice_scope='exclusive')
    narrator = await repo.ensure_default_narrator('w', 'exclusive')
    segments = await repo.bulk_insert_segments([dict(chapter_uuid=chapter.chapter_uuid,chunk_index=0,
        segment_order=0,character_uuid=narrator.character_uuid,text_content='Exact, canonical text!')],
        expected_generation=0, completed_chunk=0)
    await repo.finalize_chapter_review(chapter.chapter_uuid, 0)
    return manager, repo, await repo.get_chapter_by_uuid(chapter.chapter_uuid), narrator, segments[0]


@pytest.mark.asyncio
async def test_review_cas_claim_revocation_and_mastering(tmp_path):
    manager, repo, ch, narrator, segment = await prepared(tmp_path)
    assert ch.review_phase == 'AWAITING_APPROVAL' and not ch.narration_authorized
    assert not await repo.claim_segment(segment.segment_uuid,0,1,'worker',narrator.character_uuid,1)
    assert not await repo.claim_mastering(ch.chapter_uuid,0)
    with pytest.raises(ReviewConflict):
        await repo.approve_chapter_review(ch.chapter_uuid,0,0)
    results = await asyncio.gather(*[repo.approve_chapter_review(ch.chapter_uuid,0,1) for _ in range(2)])
    assert sorted(results) == [False,True]
    assert not await repo.claim_segment(segment.segment_uuid,0,1,'worker','foreign',1)
    assert not await repo.claim_segment(segment.segment_uuid,0,1,'worker',narrator.character_uuid,0)
    claim = await repo.claim_segment(segment.segment_uuid,0,1,'worker',narrator.character_uuid,1)
    assert claim
    await repo.return_chapter_to_review(ch.chapter_uuid,0,1)
    assert not await repo.commit_segment_ready(segment.segment_uuid,claim,'late.mp3',100)
    assert not (await repo.record_segment_retry_or_failure(segment.segment_uuid,claim,'NETWORK','late',True)).retryable
    assert not await repo.claim_mastering(ch.chapter_uuid,1)
    assert await repo.bulk_insert_segments([dict(chapter_uuid=ch.chapter_uuid,chunk_index=0,segment_order=1,
        character_uuid=narrator.character_uuid,text_content='Late analysis')],expected_generation=0) == []
    current = await repo.get_chapter_by_uuid(ch.chapter_uuid)
    assert current.review_phase == 'AWAITING_APPROVAL' and current.run_generation == 1
    await repo.approve_chapter_review(ch.chapter_uuid,1,2)
    claim = await repo.claim_segment(segment.segment_uuid,1,1,'worker',narrator.character_uuid,2)
    assert await repo.commit_segment_ready(segment.segment_uuid,claim,'current.mp3',100)
    master = await repo.claim_mastering(ch.chapter_uuid,1)
    assert master and not await repo.commit_master_completed(ch.chapter_uuid,master.master_version,'unclaimed.mp3')
    await repo.return_chapter_to_review(ch.chapter_uuid,1,2)
    assert not await repo.commit_master_completed(ch.chapter_uuid,master,'late-master.mp3')
    assert not await repo.fail_mastering(ch.chapter_uuid,master,'ERROR','late')
    async with manager.get_connection() as conn:
        assert not await (await conn.execute('PRAGMA foreign_key_check')).fetchall()


@pytest.mark.asyncio
async def test_partial_finalization_and_stale_analysis_rejected(tmp_path):
    _, repo, ch, narrator, _ = await prepared(tmp_path)
    await repo.replace_chapter_generation(ch.chapter_uuid,1,2)
    await repo.ensure_default_narrator('w',(await repo.get_chapter_by_uuid(ch.chapter_uuid)).voice_scope)
    with pytest.raises(ReviewConflict):
        await repo.finalize_chapter_review(ch.chapter_uuid,1)
    with pytest.raises(ReviewConflict):
        await repo.finalize_chapter_review(ch.chapter_uuid,0)
    assert not (await repo.get_chapter_by_uuid(ch.chapter_uuid)).analysis_completed_at


@pytest.mark.asyncio
async def test_migration_preserves_completed_and_isolates_legacy_scope(tmp_path):
    path = tmp_path / 'legacy.db'
    master = tmp_path / 'master.mp3'
    master.write_bytes(b'existing master')
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.execute('INSERT INTO schema_migrations(version) VALUES(1)')
        conn.execute('INSERT INTO schema_migrations(version) VALUES(2)')
        conn.execute("INSERT INTO works(work_id,title,author) VALUES('w','Title','Author')")
        conn.execute("""INSERT INTO characters(character_uuid,work_id,character_key,canonical_name,is_narrator,voice_instruct,reference_text)
            VALUES('n','w','char_narrator','Narrator',1,'Valid narrator instruction','A sufficiently long reference text.')""")
        for number, status in enumerate(('COMPLETED','STREAMING','STREAMING')):
            conn.execute("""INSERT INTO chapters(chapter_uuid,work_id,chapter_id,chapter_title,source_url,status,
                total_chunks,llm_completed_chunks,total_segments,master_mp3_path) VALUES(?,'w',?,'Chapter','url',?,1,1,1,?)""",
                (str(number),str(number),status,str(master) if number == 0 else None))
            conn.execute("""INSERT INTO segments(segment_uuid,chapter_uuid,chunk_index,segment_order,character_uuid,text_content)
                VALUES(?,?,0,0,'n','Canonical text.')""", ('s'+str(number),str(number)))
    manager = DatabaseManager(str(path))
    await manager.init_db()
    await manager.init_db()
    repo = AudiobookRepository(manager)
    completed = await repo.get_chapter_by_uuid('0')
    assert completed.review_phase == 'COMPLETED' and completed.approval_valid
    assert completed.master_mp3_path == str(master)
    one, two = await repo.get_chapter_by_uuid('1'), await repo.get_chapter_by_uuid('2')
    assert one.review_phase == two.review_phase == 'AWAITING_APPROVAL'
    assert one.voice_scope and two.voice_scope and one.voice_scope != two.voice_scope
    assert (await repo.get_segments_for_chapter('0'))[0].character_uuid == 'n'
    assert (await repo.get_segments_for_chapter('1'))[0].character_uuid != 'n'
    assert not one.approval_valid and not two.approval_valid
    async with manager.get_connection() as conn:
        assert not await (await conn.execute('PRAGMA foreign_key_check')).fetchall()


@pytest.mark.asyncio
async def test_multichunk_analysis_workers_and_restart_never_dispatch_before_approval(tmp_path):
    manager = DatabaseManager(str(tmp_path / 'multi.db'))
    await manager.init_db()
    repo = AudiobookRepository(manager)
    await repo.upsert_work('w','Title','Author',[])
    chapter = await repo.upsert_chapter('w','c',1,'Chapter','url',2,voice_scope='fresh')
    narrator = await repo.ensure_default_narrator('w','fresh')
    texts = ['Narration, unchanged.','"A late speaker!"']
    chunks = [NarrativeChunk(chunk_index=i,is_fast_start=i==0,is_last_chunk=i==1,word_count=3,
        start_paragraph_index=i,end_paragraph_index=i,formatted_text=text,
        paragraphs=[NormalizedParagraph(paragraph_index=i,text=text,word_count=3)]) for i,text in enumerate(texts)]
    analyzer = AsyncMock()
    analyzer.analyze_chunk.side_effect = [
        ChunkAnalysisResult(new_characters=[],character_updates=[],segments=[ScriptSegmentSpec(segment_order=0,
            paragraph_index=0,speaker_id='char_narrator',text=texts[0],is_paragraph_end=True)]),
        ChunkAnalysisResult(new_characters=[NewCharacterSpec(character_id='char_late',canonical_name='Late',aliases=[],
            gender='neutral',voice_instruct='A clear expressive speaking voice.',reference_sample_text='A sufficiently long spoken reference sample.')],
            character_updates=[],segments=[ScriptSegmentSpec(segment_order=0,paragraph_index=1,
            speaker_id='char_late',text=texts[1],is_paragraph_end=True)])]
    calls = []
    def unavailable(request):
        calls.append(request.url.path)
        return httpx.Response(400,json={'detail':'TTS unavailable'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable)) as client:
        orch = JobOrchestrator(repo,ChapterEventBus(),analyzer,tts_http_client=client,data_dir=str(tmp_path))
        await orch.start()
        try:
            await orch._run_chapter_llm_pipeline(chapter.chapter_uuid,'w',chunks)
            current = await repo.get_chapter_by_uuid(chapter.chapter_uuid)
            assert current.review_phase == 'AWAITING_APPROVAL' and current.total_segments == 2
            assert [s.text_content for s in await repo.get_segments_for_chapter(chapter.chapter_uuid)] == texts
            assert len(await repo.get_characters_for_chapter(chapter.chapter_uuid)) == 2
            segment = (await repo.get_segments_for_chapter(chapter.chapter_uuid))[0]
            await orch.tts_priority_queue.put(TTSTask(priority_tier=2,distance_key=0,chapter_uuid=chapter.chapter_uuid,
                segment_uuid=segment.segment_uuid,character_uuid=narrator.character_uuid,approved_review_revision=1))
            await asyncio.wait_for(orch.tts_priority_queue.join(),3)
            await orch.recover_interrupted_jobs()
            await asyncio.wait_for(orch.tts_priority_queue.join(),3)
            assert calls == []
            await repo.approve_chapter_review(chapter.chapter_uuid,0,1)
            await orch.schedule_approved_chapter(chapter.chapter_uuid)
            await asyncio.wait_for(orch.tts_priority_queue.join(),5)
            assert calls and calls[0] == '/internal/tts/create-voice'
            assert analyzer.analyze_chunk.await_count == 2
        finally:
            await orch.stop()


@pytest.mark.asyncio
async def test_corrections_preserve_text_and_scope_and_reject_stale_speaker(tmp_path):
    _,repo,ch,narrator,segment = await prepared(tmp_path)
    baseline = (segment.segment_uuid,segment.text_content,segment.paragraph_index,segment.chunk_index,
        segment.segment_order,segment.delivery_instruct,segment.is_paragraph_end)
    await repo.rename_review_character(ch.chapter_uuid,narrator.character_uuid,0,1,'Storyteller')
    renamed = await repo.get_character_by_uuid(narrator.character_uuid)
    assert renamed.character_key == narrator.character_key and renamed.is_narrator
    with pytest.raises(ReviewConflict):
        await repo.add_review_character(ch.chapter_uuid,0,1,'Stale','A valid new voice description.')
    await repo.add_review_character(ch.chapter_uuid,0,2,'Hero','A valid expressive voice description.')
    hero = next(c for c in await repo.get_characters_for_chapter(ch.chapter_uuid) if not c.is_narrator)
    await repo.reassign_review_speakers(ch.chapter_uuid,0,3,[segment.segment_uuid],hero.character_uuid)
    assert not await repo.reassign_review_speakers(ch.chapter_uuid,0,4,[segment.segment_uuid],hero.character_uuid)
    with pytest.raises(ReviewConflict):
        await repo.reassign_review_speakers(ch.chapter_uuid,0,4,[segment.segment_uuid,segment.segment_uuid],narrator.character_uuid)
    with pytest.raises(ReviewConflict):
        await repo.delete_review_character(ch.chapter_uuid,hero.character_uuid,0,4)
    with pytest.raises(ReviewConflict):
        await repo.delete_review_character(ch.chapter_uuid,narrator.character_uuid,0,4)
    with pytest.raises(ReviewConflict):
        await repo.merge_review_characters(ch.chapter_uuid,0,4,hero.character_uuid,narrator.character_uuid)
    await repo.add_review_character(ch.chapter_uuid,0,4,'Other','A different expressive voice description.')
    other = next(c for c in await repo.get_characters_for_chapter(ch.chapter_uuid) if c.canonical_name == 'Other')
    await repo.merge_review_characters(ch.chapter_uuid,0,5,hero.character_uuid,other.character_uuid)
    assert not await repo.get_character_by_uuid(hero.character_uuid)
    assert (await repo.get_character_by_uuid(other.character_uuid)).voice_instruct == other.voice_instruct
    foreign = await repo.ensure_default_narrator('w','foreign')
    with pytest.raises(ReviewConflict):
        await repo.rename_review_character(ch.chapter_uuid,foreign.character_uuid,0,6,'Forbidden')
    with pytest.raises(ReviewConflict):
        await repo.reassign_review_speakers(ch.chapter_uuid,0,6,['foreign-segment'],other.character_uuid)
    result = await repo.get_segment_by_uuid(segment.segment_uuid)
    assert (result.segment_uuid,result.text_content,result.paragraph_index,result.chunk_index,
        result.segment_order,result.delivery_instruct,result.is_paragraph_end) == baseline
    await repo.approve_chapter_review(ch.chapter_uuid,0,6)
    assert not await repo.claim_segment(segment.segment_uuid,0,1,'old-speaker',narrator.character_uuid,6)
    assert await repo.claim_segment(segment.segment_uuid,0,1,'new-speaker',other.character_uuid,6)
