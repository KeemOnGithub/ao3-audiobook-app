"""Unit and integration tests for Phase 4: SQLite WAL, Data Models, and Orchestration."""

import asyncio
import json
import os
import shutil
import sqlite3
import tempfile
from unittest.mock import AsyncMock
import pytest
import httpx


from backend.app.db.connection import DatabaseManager, SCHEMA_SQL
from backend.app.db.repository import AudiobookRepository
from backend.app.db.review_repository import ReviewConflict
from backend.app.orchestration.event_bus import ChapterEventBus
from backend.app.orchestration.orchestrator import JobOrchestrator
from backend.app.orchestration.queue_models import TTSTask
from backend.app.scripting.analyzer import LLMOutputTruncated, LLMScriptAnalyzer
from backend.app.scripting.schemas import ChunkAnalysisResult, NewCharacterSpec, ScriptSegmentSpec
from backend.tests.fixtures import HTML_STANDARD_MULTI_CHAPTER
from tts_service.app import app as tts_app
import tts_service.app as tts_app_module
from tts_service.models import TTSModelManager


async def approve_fixture(repo, chapter):
    async with repo.db_manager.get_connection() as conn:
        await conn.execute('UPDATE chapters SET llm_completed_chunks=total_chunks WHERE chapter_uuid=?',(chapter.chapter_uuid,))
        await conn.commit()
    await repo.finalize_chapter_review(chapter.chapter_uuid,chapter.run_generation)
    current=await repo.get_chapter_by_uuid(chapter.chapter_uuid)
    await repo.approve_chapter_review(chapter.chapter_uuid,current.run_generation,current.review_revision)
    return await repo.get_chapter_by_uuid(chapter.chapter_uuid)


@pytest.fixture
def temp_env():
    d = tempfile.mkdtemp(prefix="orch_test_")
    db_path = os.path.join(d, "test_audiobook.db")
    yield d, db_path
    shutil.rmtree(d, ignore_errors=True)


@pytest.mark.asyncio
async def test_new_load_supersedes_old_work_and_survives_restart(temp_env):
    directory, db_path = temp_env
    manager = DatabaseManager(db_path)
    await manager.init_db()
    repo = AudiobookRepository(manager)
    await repo.upsert_work("old", "Old", "Author", [])
    completed = await repo.upsert_chapter("old", "completed", 2, "Completed", "http://saved", 1)
    async with manager.get_connection() as conn:
        await conn.execute("UPDATE chapters SET status = 'COMPLETED', master_mp3_path = 'master.mp3' WHERE chapter_uuid = ?", (completed.chapter_uuid,))
        await conn.commit()
    chapter = await repo.upsert_chapter("old", "old-chapter", 1, "Old", "http://old", 1)
    char = await repo.ensure_default_narrator("old", chapter.voice_scope)
    segments = await repo.bulk_insert_segments([
        dict(chapter_uuid=chapter.chapter_uuid, chunk_index=0, segment_order=i,
             character_uuid=char.character_uuid, text_content="Text", is_paragraph_end=1,
             voice_version_used=1) for i in range(3)
    ])
    chapter = await approve_fixture(repo,chapter)
    ready_claim = await repo.claim_segment(segments[0].segment_uuid, 0, 1, "ready",char.character_uuid,chapter.review_revision)
    assert await repo.commit_segment_ready(segments[0].segment_uuid, ready_claim, "saved.mp3", 100)
    inflight = await repo.claim_segment(segments[1].segment_uuid, 0, 1, "inflight",char.character_uuid,chapter.review_revision)
    analyzer = AsyncMock()
    orch = JobOrchestrator(repo, ChapterEventBus(), analyzer, data_dir=directory)
    orch._run_chapter_llm_pipeline = AsyncMock()
    try:
        loaded = await orch.ingest_chapter(
            "https://archiveofourown.org/works/12345/chapters/67890",
            html_override=HTML_STANDARD_MULTI_CHAPTER,
        )
        assert loaded.chapter_uuid != chapter.chapter_uuid
        old = await repo.get_chapter_by_uuid(chapter.chapter_uuid)
        assert old.status == "FAILED"
        assert "SUPERSEDED" in old.error_message
        assert old.run_generation == 1
        saved = await repo.get_chapter_by_uuid(completed.chapter_uuid)
        assert saved.status == "COMPLETED" and saved.master_mp3_path == "master.mp3"
        preserved = await repo.get_segment_by_uuid(segments[0].segment_uuid)
        assert preserved.status == "READY" and preserved.audio_path == "saved.mp3"
        assert not await repo.claim_segment(segments[2].segment_uuid, 0, 1, "stale")
        assert not await repo.commit_segment_ready(segments[1].segment_uuid, inflight, "late.mp3", 100)
        result = await repo.record_segment_retry_or_failure(
            segments[1].segment_uuid, inflight, "NETWORK", "late", True,
        )
        assert result.status != "QUEUED"
        restarted = JobOrchestrator(repo, ChapterEventBus(), analyzer, data_dir=directory)
        await restarted.recover_interrupted_jobs()
        assert restarted.tts_priority_queue.empty()
        assert (await repo.get_chapter_by_uuid(chapter.chapter_uuid)).error_message == old.error_message
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_supersession_cancels_local_http_wait(temp_env):
    _, db_path = temp_env
    manager = DatabaseManager(db_path)
    await manager.init_db()
    repo = AudiobookRepository(manager)
    await repo.upsert_work("w", "Title", "Author", [])
    ch = await repo.upsert_chapter("w", "c", 1, "Chapter", "url", 1)
    narrator=await repo.ensure_default_narrator('w',ch.voice_scope)
    await repo.bulk_insert_segments([dict(chapter_uuid=ch.chapter_uuid,chunk_index=0,segment_order=0,
        character_uuid=narrator.character_uuid,text_content='An approved segment.')])
    ch=await approve_fixture(repo,ch)
    orch = JobOrchestrator(repo, ChapterEventBus(), AsyncMock())
    started = asyncio.Event()

    async def post(*args, **kwargs):
        started.set()
        await asyncio.Future()

    client = AsyncMock()
    client.post.side_effect = post
    waiting = asyncio.create_task(orch._chapter_post(ch.chapter_uuid, 0, client, "http://tts"))
    await started.wait()
    await repo.supersede_active_chapters()
    for task in list(orch._chapter_requests[ch.chapter_uuid]):
        task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert not orch._chapter_requests[ch.chapter_uuid]


@pytest.mark.asyncio
async def test_sqlite_schema_and_narrator_init(temp_env):
    """Verify SQLite WAL pragmas, tables, and auto-seeded narrator."""
    _, db_path = temp_env
    db_mgr = DatabaseManager(db_path)
    await db_mgr.init_db()

    repo = AudiobookRepository(db_mgr)
    work = await repo.upsert_work(
        work_id="work_001",
        title="Test Work",
        author="Author Name",
        fandoms=["Fandom A"],
    )
    assert work.work_id == "work_001"

    # Default narrator should exist
    chars = await repo.get_characters_for_work("work_001")
    assert len(chars) == 1
    assert chars[0].character_key == "char_narrator"
    assert chars[0].is_narrator == 1


@pytest.mark.asyncio
async def test_priority_queue_ordering():
    """Verify TTSTask ordering across priority tiers and playhead distance keys."""
    pq: asyncio.PriorityQueue[TTSTask] = asyncio.PriorityQueue()

    # Push Standard (Tier 2) task first
    await pq.put(TTSTask(priority_tier=2, distance_key=10, segment_order=10))
    # Push Fast-Start (Tier 1) task
    await pq.put(TTSTask(priority_tier=1, distance_key=0, segment_order=0))
    # Push Hot-Swap (Tier 0) task ahead of playhead
    await pq.put(TTSTask(priority_tier=0, distance_key=2, segment_order=5))
    # Push Hot-Swap (Tier 0) wrap-around task (behind playhead)
    await pq.put(TTSTask(priority_tier=0, distance_key=100001, segment_order=1))

    # Popped order must be:
    # 1. Tier 0 distance 2
    # 2. Tier 0 distance 100001
    # 3. Tier 1 distance 0
    # 4. Tier 2 distance 10
    t1 = await pq.get()
    assert t1.priority_tier == 0 and t1.distance_key == 2
    t2 = await pq.get()
    assert t2.priority_tier == 0 and t2.distance_key == 100001
    t3 = await pq.get()
    assert t3.priority_tier == 1 and t3.distance_key == 0
    t4 = await pq.get()
    assert t4.priority_tier == 2 and t4.distance_key == 10


@pytest.mark.asyncio
async def test_event_bus_pubsub_and_replay():
    """Verify ChapterEventBus pub/sub distribution and event history replay."""
    bus = ChapterEventBus()
    chapter_uuid = "chap-uuid-123"

    # Emit before subscribe (historical event)
    await bus.emit(chapter_uuid, "segment_ready", {"segment_order": 0})

    # Subscriber should receive historical event immediately
    q, snapshot_req = await bus.subscribe(chapter_uuid)
    assert not snapshot_req
    first_event = await asyncio.wait_for(q.get(), timeout=1.0)
    assert first_event["event"] == "segment_ready"
    assert first_event["data"]["segment_order"] == 0

    # Emit live event
    await bus.emit(chapter_uuid, "chapter_mastering", {"status": "MASTERING"})
    second_event = await asyncio.wait_for(q.get(), timeout=1.0)
    assert second_event["event"] == "chapter_mastering"

    await bus.unsubscribe(chapter_uuid, q)


@pytest.mark.asyncio
async def test_stale_task_rejection_on_hot_swap(temp_env):
    """Verify that update_segment_ready rejects committing if a hot-swap changed the version."""
    _, db_path = temp_env
    db_mgr = DatabaseManager(db_path)
    await db_mgr.init_db()
    repo = AudiobookRepository(db_mgr)

    await repo.upsert_work("work1", "Title", "Author", [])
    ch = await repo.upsert_chapter("work1", "chap1", 1, "Ch 1", "http://test", 1)
    char = await repo.insert_character("work1", "char_hero", "Hero", [], "A valid voice instruct", "A sufficiently long reference sample.", voice_version=1,voice_scope=ch.voice_scope)

    segs = await repo.bulk_insert_segments([
        {
            "chapter_uuid": ch.chapter_uuid,
            "chunk_index": 0,
            "segment_order": 0,
            "character_uuid": char.character_uuid,
            "text_content": "Line of text",
            "is_paragraph_end": 1,
            "voice_version_used": 1,
        }
    ])
    seg_uuid = segs[0].segment_uuid
    await repo.ensure_default_narrator('work1',ch.voice_scope)
    ch=await approve_fixture(repo,ch)
    claim=await repo.claim_segment(seg_uuid,0,1,'worker',char.character_uuid,ch.review_revision)

    # Now a hot-swap happens, bumping character version to 2
    await repo.update_character_voice(
        character_uuid=char.character_uuid,
        voice_instruct="new instruct",
        voice_status="READY",
        voice_version=2,
    )

    # Worker finishes synthesis for old version 1 -> commit must be rejected!
    committed = await repo.commit_segment_ready(
        segment_uuid=seg_uuid,
        claim=claim,
        audio_path="/fake/path.mp3",
        duration_ms=1000,
    )
    assert committed is False

    # Unclaimed publication is forbidden even for a matching current version.
    committed_v2 = await repo.update_segment_ready(
        segment_uuid=seg_uuid,
        character_uuid=char.character_uuid,
        target_voice_version=2,
        audio_path="/fake/path_v2.mp3",
        duration_ms=1000,
    )
    assert committed_v2 is False


@pytest.mark.asyncio
async def test_same_paragraph_cross_chunk_whitespace_is_canonical(temp_env):
    """Restore only continuation spacing before persisting and streaming script."""
    from backend.app.ingestion.schemas import NarrativeChunk, NormalizedParagraph

    directory, db_path = temp_env
    manager = DatabaseManager(db_path)
    await manager.init_db()
    repo = AudiobookRepository(manager)
    await repo.upsert_work("cross-chunk", "Quoted", "Author", [])
    chapter = await repo.upsert_chapter("cross-chunk", "one", 1, "One", "saved", 3)
    await repo.ensure_default_narrator(chapter.work_id, chapter.voice_scope)
    fragments = [(4, '"The first sentence.'), (4, 'The second sentence."'), (5, 'Afterward.')]
    chunks = [
        NarrativeChunk(
            chunk_index=i, is_fast_start=i == 0, is_last_chunk=i == len(fragments) - 1,
            word_count=len(text.split()), start_paragraph_index=index, end_paragraph_index=index,
            paragraphs=[NormalizedParagraph(paragraph_index=index, text=text, word_count=len(text.split()))],
            formatted_text=text,
        )
        for i, (index, text) in enumerate(fragments)
    ]
    requests = []

    def analyze(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        requests.append(payload)
        paragraph = payload["chunk_paragraphs"][0]
        result = ChunkAnalysisResult(new_characters=[], character_updates=[], segments=[
            ScriptSegmentSpec(
                segment_order=0, paragraph_index=paragraph["paragraph_index"],
                speaker_id="char_narrator", text=paragraph["text"], is_paragraph_end=True,
            ),
        ])
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "stop", "message": {"content": result.model_dump_json()},
        }]})

    bus = ChapterEventBus()
    async with httpx.AsyncClient(transport=httpx.MockTransport(analyze)) as client:
        analyzer = LLMScriptAnalyzer(client=client, max_retries=0)
        orch = JobOrchestrator(repo, bus, analyzer, data_dir=directory)
        await orch._run_chapter_llm_pipeline(chapter.chapter_uuid, chapter.work_id, chunks)

    stored = await repo.get_segments_for_chapter(chapter.chapter_uuid)
    expected = ['"The first sentence.', ' The second sentence."', 'Afterward.']
    assert [segment.text_content for segment in stored] == expected
    assert ''.join(segment.text_content for segment in stored if segment.paragraph_index == 4) == (
        '"The first sentence. The second sentence."'
    )
    assert [segment.paragraph_index for segment in stored] == [4, 4, 5]
    assert [bool(segment.is_paragraph_end) for segment in stored] == [False, True, True]
    assert [segment.segment_order for segment in stored] == [0, 1, 2]
    assert [payload["chunk_paragraphs"][0]["text"] for payload in requests] == [text for _, text in fragments]
    assert requests[1]["previous_context_segments"][0]["text"] == expected[0]
    events, _ = await bus.subscribe(chapter.chapter_uuid)
    streamed = []
    streamed_ends = []
    while not events.empty():
        event = events.get_nowait()
        if event["event"] == "script_chunk_ready":
            streamed.extend(segment["text"] for segment in event["data"]["segments"])
            streamed_ends.extend(segment["is_paragraph_end"] for segment in event["data"]["segments"])
    assert streamed == expected
    assert streamed_ends == [False, True, True]
    assert orch.tts_priority_queue.empty()
    assert (await repo.get_chapter_by_uuid(chapter.chapter_uuid)).review_phase == 'AWAITING_APPROVAL'


@pytest.mark.asyncio
async def test_canonical_speech_payload_survives_retry_and_audio_regeneration(temp_env):
    directory, db_path = temp_env
    manager = DatabaseManager(db_path)
    await manager.init_db()
    repo = AudiobookRepository(manager)
    await repo.upsert_work("quoted", "Quoted", "Author", [])
    chapter = await repo.upsert_chapter("quoted", "one", 1, "One", "saved", 1)
    narrator = await repo.ensure_default_narrator("quoted", chapter.voice_scope)
    embedding = os.path.join(directory, "mock.pt")
    with open(embedding, "wb") as stream:
        stream.write(b"mock embedding")
    await repo.update_character_voice(narrator.character_uuid, narrator.voice_instruct, "READY", embedding_pt_path=embedding)
    text = '\"Wait,\" she said, \"don\'t go...\"'
    segments = await repo.bulk_insert_segments([dict(
        chapter_uuid=chapter.chapter_uuid, chunk_index=0, segment_order=0,
        character_uuid=narrator.character_uuid, text_content=text,
        is_paragraph_end=1, voice_version_used=1,
    )])
    payloads = []

    def synthesize(request):
        payloads.append(json.loads(request.content))
        # Exercise retry without generating audio or invoking any remote service.
        return httpx.Response(503 if len(payloads) % 2 else 400, json={"detail": "controlled failure"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(synthesize)) as client:
        orch = JobOrchestrator(repo, ChapterEventBus(), AsyncMock(), data_dir=directory, tts_http_client=client)
        orch._is_running = True
        worker = asyncio.create_task(orch._tts_worker_loop(0))
        try:
            for generation in (0, 1):
                if generation:
                    await repo.replace_chapter_generation(chapter.chapter_uuid, generation, 1, "AUDIO_ONLY")
                else:
                    async with manager.get_connection() as conn:
                        await conn.execute('UPDATE chapters SET llm_completed_chunks=1 WHERE chapter_uuid=?',(chapter.chapter_uuid,))
                        await conn.commit()
                    await repo.finalize_chapter_review(chapter.chapter_uuid,0)
                current=await repo.get_chapter_by_uuid(chapter.chapter_uuid)
                await repo.approve_chapter_review(chapter.chapter_uuid,current.run_generation,current.review_revision)
                await orch.tts_priority_queue.put(TTSTask(
                    priority_tier=1, distance_key=0, segment_order=0,
                    segment_uuid=segments[0].segment_uuid, chapter_uuid=chapter.chapter_uuid,
                    character_uuid=narrator.character_uuid, target_voice_version=1,
                    run_generation=generation,
                    approved_review_revision=current.review_revision,
                ))
                await asyncio.wait_for(orch.tts_priority_queue.join(), timeout=20)
                assert (await repo.get_segment_by_uuid(segments[0].segment_uuid)).text_content == text
                assert (await repo.get_chapter_by_uuid(chapter.chapter_uuid)).status == "FAILED"
            assert [payload["text"] for payload in payloads] == [text] * 4
        finally:
            orch._is_running = False
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_end_to_end_orchestration_flow(temp_env):
    """Verify complete orchestration loop with mock LLM and in-memory TTS service."""
    data_dir, db_path = temp_env
    db_mgr = DatabaseManager(db_path)
    await db_mgr.init_db()
    repo = AudiobookRepository(db_mgr)
    bus = ChapterEventBus()

    # Configure mock TTS service
    tts_app.state.data_dir = data_dir
    tts_app_module.model_manager = TTSModelManager(use_mock=True)
    tts_transport = httpx.ASGITransport(app=tts_app)
    tts_client = httpx.AsyncClient(transport=tts_transport, base_url="http://test")
    speech_requests = []

    async def capture_speech(request):
        if request.url.path.endswith("/synthesize-line"):
            speech_requests.append(json.loads(request.content))

    tts_client.event_hooks["request"] = [capture_speech]

    # Configure offline mock LLM client returning valid deconstructed script
    mock_llm_json = {
        "id": "mock-llm-1",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps({
                        "new_characters": [
                            {
                                "character_id": "char_hero",
                                "canonical_name": "Hero",
                                "aliases": ["Hero"],
                                "gender": "male",
                                "voice_instruct": "A young heroic voice.",
                                "reference_sample_text": "Are you certain we are on the right path?",
                            },
                            {
                                "character_id": "char_villain",
                                "canonical_name": "Villain",
                                "aliases": ["Villain"],
                                "gender": "male",
                                "voice_instruct": "A calm, calculated villainous voice.",
                                "reference_sample_text": "Certainty is a luxury we cannot afford.",
                            },
                        ],
                        "character_updates": [],
                        "segments": [
                            {
                                "segment_order": 0,
                                "paragraph_index": 0,
                                "speaker_id": "char_narrator",
                                "text": "The morning light broke through the dense canopy of the enchanted forest.",
                                "is_paragraph_end": True,
                            },
                            {
                                "segment_order": 1,
                                "paragraph_index": 1,
                                "speaker_id": "char_hero",
                                "text": '"Are you certain we are on the right path?"',
                                "is_paragraph_end": False,
                            },
                            {
                                "segment_order": 2,
                                "paragraph_index": 1,
                                "speaker_id": "char_narrator",
                                "text": "asked Hero, looking back over his shoulder.",
                                "is_paragraph_end": True,
                            },
                            {
                                "segment_order": 3,
                                "paragraph_index": 2,
                                "speaker_id": "char_villain",
                                "text": '"Certainty is a luxury we cannot afford,"',
                                "is_paragraph_end": False,
                            },
                            {
                                "segment_order": 4,
                                "paragraph_index": 2,
                                "speaker_id": "char_narrator",
                                "text": "replied Villain calmly.",
                                "is_paragraph_end": True,
                            },
                            {
                                "segment_order": 5,
                                "paragraph_index": 3,
                                "speaker_id": "char_narrator",
                                "text": "Later that evening, after the storm had cleared, they made camp near the riverbank.",
                                "is_paragraph_end": True,
                            },
                        ],
                    })
                },
            }
        ]
    }
    llm_transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=mock_llm_json, request=req)
    )
    llm_mock_client = httpx.AsyncClient(transport=llm_transport)
    analyzer = LLMScriptAnalyzer(client=llm_mock_client, max_retries=0)


    orchestrator = JobOrchestrator(
        repository=repo,
        event_bus=bus,
        llm_analyzer=analyzer,
        tts_base_url="http://test",
        tts_concurrency=2,
        data_dir=data_dir,
        tts_http_client=tts_client,
    )

    await orchestrator.start()

    try:
        # Ingest chapter using offline HTML fixture
        url = "https://archiveofourown.org/works/12345/chapters/987654"
        chapter = await orchestrator.ingest_chapter(
            url=url,
            force_regenerate=True,
            html_override=HTML_STANDARD_MULTI_CHAPTER,
        )

        assert chapter.chapter_uuid is not None
        await orchestrator._scripting_tasks[chapter.chapter_uuid]
        review=await repo.get_chapter_by_uuid(chapter.chapter_uuid)
        assert review.review_phase == 'AWAITING_APPROVAL' and not speech_requests
        await repo.approve_chapter_review(chapter.chapter_uuid,review.run_generation,review.review_revision)
        await orchestrator.schedule_approved_chapter(chapter.chapter_uuid)

        # Wait for all segments to be processed and mastered
        for _ in range(50):
            ch_status = await repo.get_chapter_by_uuid(chapter.chapter_uuid)
            if ch_status and ch_status.status in ("COMPLETED", "FAILED"):
                break
            await asyncio.sleep(0.2)

        final_ch = await repo.get_chapter_by_uuid(chapter.chapter_uuid)
        assert final_ch.status == "COMPLETED"
        assert final_ch.completed_segments > 0
        assert final_ch.master_mp3_path is not None
        assert os.path.exists(final_ch.master_mp3_path)

        stored = await repo.get_segments_for_chapter(chapter.chapter_uuid)
        canonical = [segment.text_content for segment in stored]
        assert canonical[1] == '"Are you certain we are on the right path?"'
        assert canonical[3] == '"Certainty is a luxury we cannot afford,"'
        assert {payload["text"] for payload in speech_requests} == set(canonical)
        events, _ = await bus.subscribe(chapter.chapter_uuid)
        streamed = []
        while not events.empty():
            event = events.get_nowait()
            if event["event"] == "script_chunk_ready":
                streamed.extend(segment["text"] for segment in event["data"]["segments"])
        assert streamed == canonical

        # Test Voice Hot-Swap
        chars = await repo.get_characters_for_work(final_ch.work_id)
        narrator = chars[0]
        with pytest.raises(ReviewConflict):
            await orchestrator.handle_voice_hotswap(narrator.character_uuid,'A refreshed deeper narrator tone.',final_ch.chapter_uuid,0)

    finally:
        await orchestrator.stop()
        await tts_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("truncated", [True, False])
async def test_llm_pipeline_failure_code_is_persisted_and_emitted(temp_env, truncated):
    data_dir, db_path = temp_env
    db_mgr = DatabaseManager(db_path)
    await db_mgr.init_db()
    repo = AudiobookRepository(db_mgr)
    bus = ChapterEventBus()
    analyzer = AsyncMock(spec=LLMScriptAnalyzer)
    message = "LLM output truncated at max_tokens=2048" if truncated else "LLM analysis failed"
    analyzer.analyze_chunk.side_effect = LLMOutputTruncated(message) if truncated else RuntimeError(message)
    orchestrator = JobOrchestrator(repo, bus, analyzer, data_dir=data_dir)

    try:
        chapter = await orchestrator.ingest_chapter(
            "https://archiveofourown.org/works/12345/chapters/987654",
            html_override=HTML_STANDARD_MULTI_CHAPTER,
        )
        await orchestrator._scripting_tasks[chapter.chapter_uuid]
        expected_code = "LLM_OUTPUT_TRUNCATED" if truncated else "LLM_DECONSTRUCTION_FAILED"
        failed = await repo.get_chapter_by_uuid(chapter.chapter_uuid)
        assert failed.status == "FAILED"
        assert failed.error_message == f"[{expected_code}] {message}"
        assert await repo.get_segments_for_chapter(chapter.chapter_uuid) == []
        assert orchestrator.tts_priority_queue.empty()

        events, _ = await bus.subscribe(chapter.chapter_uuid)
        event = events.get_nowait()
        assert event["event"] == "chapter_failed"
        assert event["data"]["error_code"] == expected_code
        assert event["data"]["error_message"] == message
        assert event["data"]["run_generation"] == chapter.run_generation
        assert event["data"]["state_revision"] == failed.state_revision
    finally:
        await orchestrator.stop()


@pytest.mark.asyncio
async def test_repeated_url_load_uses_fresh_voice_scope(temp_env):
    """Full replacement detaches old profiles instead of recycling their artifacts."""
    data_dir, db_path = temp_env
    db_mgr = DatabaseManager(db_path)
    await db_mgr.init_db()
    repo = AudiobookRepository(db_mgr)

    await repo.upsert_work("work_repeat", "Repeat Work", "Author", [])
    first = await repo.upsert_chapter(
        "work_repeat", "chapter_repeat", 1, "Chapter", "https://archiveofourown.org/works/1", voice_scope="run-one"
    )
    first_narrator = await repo.ensure_default_narrator(first.work_id, first.voice_scope)
    await repo.update_character_voice(
        first_narrator.character_uuid,
        first_narrator.voice_instruct,
        "READY",
        embedding_pt_path=os.path.join(data_dir, "old.pt"),
        preview_mp3_path=os.path.join(data_dir, "old.mp3"),
    )

    second = await repo.replace_chapter_generation(
        first.chapter_uuid, new_generation=1, total_chunks=1, mode="FULL"
    )
    second_narrator = await repo.ensure_default_narrator(second.work_id, second.voice_scope)

    assert second.voice_scope != first.voice_scope
    assert second_narrator.character_uuid != first_narrator.character_uuid
    assert second_narrator.embedding_pt_path is None
    assert second_narrator.preview_mp3_path is None
    assert (await repo.get_characters_for_chapter(second.chapter_uuid))[0].character_uuid == second_narrator.character_uuid


@pytest.mark.asyncio
@pytest.mark.parametrize("previous_status", ["STREAMING", "COMPLETED", "FAILED"])
@pytest.mark.parametrize("force_regenerate", [False, "NONE", "FULL", True])
async def test_submitting_same_url_recreates_profiles(temp_env, previous_status, force_regenerate):
    """Default loads and full regeneration never use old profiles or script context."""
    data_dir, db_path = temp_env
    db_mgr = DatabaseManager(db_path)
    await db_mgr.init_db()
    repo = AudiobookRepository(db_mgr)
    bus = ChapterEventBus()
    analyzer = AsyncMock(spec=LLMScriptAnalyzer)
    analyzer.analyze_chunk.return_value = ChunkAnalysisResult(
        new_characters=[NewCharacterSpec(
            character_id="char_hero", canonical_name="Hero", aliases=["Hero"], gender="male",
            voice_instruct="A clear and warm adult baritone voice.",
            reference_sample_text="We will find the path together before the evening shadows fall.",
        )],
        character_updates=[],
        segments=[ScriptSegmentSpec(
            segment_order=0, paragraph_index=0, speaker_id="char_hero",
            text="We will find the path together.", is_paragraph_end=True,
        )],
    )
    voice_requests = []

    def create_voice(request):
        payload = json.loads(request.content)
        voice_requests.append(payload)
        voice_id = payload["character_id"]
        return httpx.Response(200, json={
            "embedding_path": os.path.join(data_dir, f"{voice_id}.pt"),
            "preview_audio_path": os.path.join(data_dir, f"{voice_id}.mp3"),
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(create_voice)) as tts_client:
        orch = JobOrchestrator(repo, bus, analyzer, data_dir=data_dir, tts_http_client=tts_client)
        try:
            url = "https://archiveofourown.org/works/12345/chapters/987654"
            first = await orch.ingest_chapter(url, html_override=HTML_STANDARD_MULTI_CHAPTER)
            await orch._scripting_tasks[first.chapter_uuid]
            old_chars = await repo.get_characters_for_chapter(first.chapter_uuid)
            old_hero = next(c for c in old_chars if c.character_key == "char_hero")
            await repo.update_character_voice(old_hero.character_uuid, "Previously edited voice prompt", "READY", voice_version=4)
            old_segments = await repo.get_segments_for_chapter(first.chapter_uuid)
            await repo.update_segment_ready(old_segments[0].segment_uuid, old_hero.character_uuid, 4, "old.mp3", 1200)
            master_path = os.path.join(data_dir, "old-master.mp3")
            with open(master_path, "wb") as stream:
                stream.write(b"old audio")
            async with db_mgr.get_connection() as conn:
                await conn.execute(
                    "UPDATE chapters SET status = ?, master_mp3_path = ?, error_message = 'old failure' WHERE chapter_uuid = ?;",
                    (previous_status, master_path, first.chapter_uuid),
                )
                await conn.commit()

            # Another chapter in the same AO3 work must not inherit this registry either.
            other = await repo.upsert_chapter(first.work_id, "other", 2, "Other", url, voice_scope="other-scope")
            other_char = await repo.insert_character(
                first.work_id, "char_other", "Other", [], "Another voice", "Another reference",
                voice_scope=other.voice_scope,
            )
            other_queue, _ = await bus.subscribe(other.chapter_uuid)

            second = await orch.ingest_chapter(url, force_regenerate, html_override=HTML_STANDARD_MULTI_CHAPTER)
            await orch._scripting_tasks[second.chapter_uuid]
            new_chars = await repo.get_characters_for_chapter(second.chapter_uuid)
            new_segments = await repo.get_segments_for_chapter(second.chapter_uuid)
            new_hero = next(c for c in new_chars if c.character_key == "char_hero")
            refreshed = await repo.get_chapter_by_uuid(second.chapter_uuid)

            assert second.run_generation == first.run_generation + 1
            assert second.voice_scope != first.voice_scope
            assert {c.character_uuid for c in old_chars}.isdisjoint(c.character_uuid for c in new_chars)
            assert {c.character_key for c in new_chars} == {"char_narrator", "char_hero"}
            assert new_hero.voice_version == 1
            assert new_hero.voice_instruct != "Previously edited voice prompt"
            assert new_hero.embedding_pt_path is None
            assert new_hero.preview_mp3_path is None
            assert voice_requests == []
            assert new_segments[0].segment_uuid != old_segments[0].segment_uuid
            assert new_segments[0].status == "QUEUED"
            assert new_segments[0].audio_path is None
            assert refreshed.master_mp3_path is None
            assert refreshed.error_message is None
            assert other_queue.empty()
            assert (await repo.get_characters_for_chapter(other.chapter_uuid))[0].character_uuid == other_char.character_uuid
            for call in analyzer.analyze_chunk.call_args_list:
                assert [c.character_id for c in call.kwargs["existing_characters"]] == ["char_narrator"]
                assert call.kwargs["previous_context_segments"] == []

            events, _ = await bus.subscribe(second.chapter_uuid)
            ready_events = []
            while not events.empty():
                event = events.get_nowait()
                if event["event"] == "character_voice_ready" and event["data"]["run_generation"] == second.run_generation:
                    ready_events.append(event["data"])
            assert ready_events == []

            # Explicit audio-only regeneration retains this run's profiles and script.
            audio_only = await orch.ingest_chapter(url, "AUDIO_ONLY", html_override=HTML_STANDARD_MULTI_CHAPTER)
            assert audio_only.voice_scope == second.voice_scope
            assert analyzer.analyze_chunk.await_count == 2
            assert voice_requests == []
            assert audio_only.review_phase == 'AWAITING_APPROVAL'
            assert [c.character_uuid for c in await repo.get_characters_for_chapter(second.chapter_uuid)] == [c.character_uuid for c in new_chars]
            assert (await repo.get_segments_for_chapter(second.chapter_uuid))[0].segment_uuid == new_segments[0].segment_uuid
        finally:
            await orch.stop()


@pytest.mark.asyncio
async def test_voice_scope_migration_preserves_existing_data(temp_env):
    """Legacy work-scoped data stays readable while new scopes allow duplicate keys."""
    _, db_path = temp_env
    legacy_schema = SCHEMA_SQL.replace("    voice_scope TEXT NOT NULL DEFAULT '',\n", "").replace(
        "UNIQUE(work_id, voice_scope, character_key)", "UNIQUE(work_id, character_key)"
    )
    with sqlite3.connect(db_path) as conn:
        conn.executescript(legacy_schema)
        conn.execute("INSERT INTO schema_migrations (version) VALUES (1);")
        conn.execute("INSERT INTO works (work_id, title, author) VALUES ('legacy', 'Title', 'Author');")
        conn.execute("INSERT INTO chapters (chapter_uuid, work_id, chapter_id, chapter_title, source_url) VALUES ('chapter', 'legacy', 'one', 'Chapter', 'url');")
        conn.execute("INSERT INTO characters (character_uuid, work_id, character_key, canonical_name, voice_instruct, reference_text) VALUES ('character', 'legacy', 'char_hero', 'Hero', 'old voice', 'old reference');")
        conn.execute("INSERT INTO segments (segment_uuid, chapter_uuid, chunk_index, segment_order, character_uuid, text_content) VALUES ('segment', 'chapter', 0, 0, 'character', 'Text');")

    db_mgr = DatabaseManager(db_path)
    await db_mgr.init_db()
    await db_mgr.init_db()
    repo = AudiobookRepository(db_mgr)
    legacy_char = next(c for c in await repo.get_characters_for_chapter('chapter') if not c.is_narrator)
    assert legacy_char.character_uuid != "character"
    assert legacy_char.voice_scope
    assert (await repo.get_segment_by_uuid("segment")).character_uuid == legacy_char.character_uuid
    new_char = await repo.insert_character("legacy", "char_hero", "Hero", [], "new voice", "new reference", voice_scope="fresh")
    assert new_char.character_uuid != legacy_char.character_uuid
    async with db_mgr.get_connection() as conn:
        assert await (await conn.execute("PRAGMA foreign_key_check;")).fetchall() == []
        assert (await (await conn.execute("PRAGMA foreign_keys;")).fetchone())[0] == 1
