"""Unit and integration tests for Phase 5: FastAPI REST Endpoints & SSE Stream."""

import asyncio
import os
import shutil
import tempfile
import pytest
import httpx

from backend.app.db.connection import DatabaseManager
from backend.app.db.repository import AudiobookRepository
from backend.app.main import app
from backend.app.orchestration.event_bus import ChapterEventBus
from backend.app.orchestration.orchestrator import JobOrchestrator
from backend.app.scripting.analyzer import LLMScriptAnalyzer
from tts_service.app import app as tts_app
from backend.tests.test_data_orchestration import approve_fixture


@pytest.fixture
def api_test_env():
    d = tempfile.mkdtemp(prefix="api_test_")
    db_path = os.path.join(d, "api_test.db")
    yield d, db_path
    shutil.rmtree(d, ignore_errors=True)


@pytest.mark.asyncio
async def test_api_rest_endpoints(api_test_env):
    """Test REST endpoints: load chapter, get state, seek playback, download."""
    data_dir, db_path = api_test_env

    # Setup database
    db_mgr = DatabaseManager(db_path)
    await db_mgr.init_db()
    repo = AudiobookRepository(db_mgr)
    bus = ChapterEventBus()

    # Setup mock TTS service
    tts_app.state.data_dir = data_dir
    tts_transport = httpx.ASGITransport(app=tts_app)
    tts_client = httpx.AsyncClient(transport=tts_transport, base_url="http://test")

    analyzer = LLMScriptAnalyzer(max_retries=0)

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

    app.state.db_manager = db_mgr
    app.state.repository = repo
    app.state.event_bus = bus
    app.state.orchestrator = orchestrator

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Invalid URL returns 400
        bad_res = await client.post("/api/v1/chapters/load", json={"ao3_url": "https://notao3.com/123"})
        assert bad_res.status_code == 400

        # Pre-seed a completed chapter in repo for state and download testing
        await repo.upsert_work("99999", "A Test Work", "An Author", ["Anime"])
        chapter = await repo.upsert_chapter("99999", "11111", 1, "The Beginning", "https://archiveofourown.org/works/99999/chapters/11111",1)
        await repo.ensure_default_narrator('99999',chapter.voice_scope)
        char = await repo.insert_character("99999", "char_alex", "Alex", [], "voice instruct", "ref text", "READY",voice_scope=chapter.voice_scope)

        # Create dummy segment audio
        dummy_seg_audio = os.path.join(data_dir, "test_seg.mp3")
        with open(dummy_seg_audio, "wb") as f:
            f.write(b"ID3" + b"\x00" * 2000)

        segs = await repo.bulk_insert_segments([
            {
                "chapter_uuid": chapter.chapter_uuid,
                "chunk_index": 0,
                "segment_order": 0,
                "character_uuid": char.character_uuid,
                "text_content": '\"Did he say \'tomorrow\'?\" she asked. *I don\'t know...*',
                "is_paragraph_end": 1,
            }
        ])
        chapter=await approve_fixture(repo,chapter)
        claim=await repo.claim_segment(segs[0].segment_uuid,0,1,'api-fixture',char.character_uuid,chapter.review_revision)
        assert await repo.commit_segment_ready(segs[0].segment_uuid,claim,dummy_seg_audio,1200)

        # 2. Chapter State
        state_res = await client.get(f"/api/v1/chapters/{chapter.chapter_uuid}/state")
        assert state_res.status_code == 200
        state_data = state_res.json()
        assert state_data["chapter_uuid"] == chapter.chapter_uuid
        assert len(state_data["segments"]) == 1
        assert state_data["segments"][0]["status"] == "READY"
        assert state_data["segments"][0]["text"] == segs[0].text_content
        assert (await repo.get_segment_by_uuid(segs[0].segment_uuid)).text_content == segs[0].text_content

        # 3. Seek Reprioritization
        seek_res = await client.post(
            f"/api/v1/chapters/{chapter.chapter_uuid}/seek",
            json={"current_playback_order": 5},
        )
        assert seek_res.status_code == 200
        assert seek_res.json()["status"] == "REPRIORITIZED"

        # 4. Byte-Range Media Streaming
        seg_uuid = segs[0].segment_uuid
        # Full content
        audio_full = await client.get(f"/api/v1/audio/segments/{seg_uuid}.mp3")
        assert audio_full.status_code == 200
        assert audio_full.headers["accept-ranges"] == "bytes"
        assert int(audio_full.headers["content-length"]) == len(audio_full.content)

        # Partial content (bytes=0-99)
        audio_range = await client.get(
            f"/api/v1/audio/segments/{seg_uuid}.mp3",
            headers={"range": "bytes=0-99"},
        )
        assert audio_range.status_code == 206
        assert len(audio_range.content) == 100
        assert "bytes 0-99/" in audio_range.headers["content-range"]

    await orchestrator.stop()
    await tts_client.aclose()


@pytest.mark.asyncio
async def test_sse_event_streaming(api_test_env):
    """Test SSE streaming handshake and broadcast delivery."""
    data_dir, db_path = api_test_env
    db_mgr = DatabaseManager(db_path)
    await db_mgr.init_db()
    repo = AudiobookRepository(db_mgr)
    bus = ChapterEventBus()

    await repo.upsert_work("w1", "Title", "Author", [])
    ch = await repo.upsert_chapter("w1", "c1", 1, "Ch 1", "http://test")

    app.state.db_manager = db_mgr
    app.state.repository = repo
    app.state.event_bus = bus
    app.state.orchestrator = JobOrchestrator(
        repository=repo,
        event_bus=bus,
        llm_analyzer=LLMScriptAnalyzer(max_retries=0),
        data_dir=data_dir,
    )

    from starlette.requests import Request
    from backend.app.api.routes import sse_event_stream

    request = Request({"type": "http", "app": app, "headers": []})
    response = await sse_event_stream(ch.chapter_uuid, request)
    stream = response.body_iterator
    assert response.media_type == "text/event-stream"
    assert await anext(stream) == "retry: 2000\n\n"

    # A live stream must survive idle time and deliver subsequent events.
    pending = asyncio.create_task(anext(stream))
    try:
        await asyncio.sleep(0.05)
        assert not pending.done()
        await bus.emit(ch.chapter_uuid, "pipeline_progress", {"stage": "SYNTHESIS"})
        event = await asyncio.wait_for(pending, timeout=1)
        assert "event: pipeline_progress" in event
        assert '"stage": "SYNTHESIS"' in event
    finally:
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await stream.aclose()


@pytest.mark.asyncio
async def test_review_api_endpoints_and_revocation(api_test_env):
    """Test public HTTP review mutations, validation, preview generation, approval, duplicate approval, and audio revocation."""
    data_dir, db_path = api_test_env
    db_mgr = DatabaseManager(db_path)
    await db_mgr.init_db()
    repo = AudiobookRepository(db_mgr)
    bus = ChapterEventBus()

    orchestrator = JobOrchestrator(
        repository=repo,
        event_bus=bus,
        llm_analyzer=LLMScriptAnalyzer(max_retries=0),
        tts_base_url="http://test",
        tts_concurrency=1,
        data_dir=data_dir,
    )

    app.state.db_manager = db_mgr
    app.state.repository = repo
    app.state.event_bus = bus
    app.state.orchestrator = orchestrator

    await repo.upsert_work("w_rev", "Review Title", "Author", [])
    chapter = await repo.upsert_chapter("w_rev", "c_rev", 1, "Review Chapter", "http://test/rev", 1, voice_scope="rev_scope")
    narrator = await repo.ensure_default_narrator("w_rev", chapter.voice_scope)
    segs = await repo.bulk_insert_segments([
        {
            "chapter_uuid": chapter.chapter_uuid,
            "chunk_index": 0,
            "segment_order": 0,
            "character_uuid": narrator.character_uuid,
            "text_content": "A canonical sentence for testing review.",
            "is_paragraph_end": 1,
        }
    ], expected_generation=chapter.run_generation, completed_chunk=0)
    await repo.finalize_chapter_review(chapter.chapter_uuid, chapter.run_generation)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Check initial review state
        res = await client.get(f"/api/v1/chapters/{chapter.chapter_uuid}/state")
        assert res.status_code == 200
        data = res.json()
        assert data["review_phase"] == "AWAITING_APPROVAL"
        assert data["narration_authorized"] is False
        assert data["can_approve"] is True
        gen = data["run_generation"]
        rev = data["review_revision"]

        # Add character
        add_res = await client.post(
            f"/api/v1/chapters/{chapter.chapter_uuid}/review/characters",
            json={
                "expected_run_generation": gen,
                "expected_review_revision": rev,
                "canonical_name": "Alice",
                "voice_instruct": "A clear bright soprano voice with distinct cadence.",
            },
        )
        assert add_res.status_code == 200
        add_data = add_res.json()
        alice = next(c for c in add_data["characters"] if c["canonical_name"] == "Alice")
        alice_uuid = alice["character_uuid"]
        rev = add_data["review_revision"]

        # Rename character
        ren_res = await client.patch(
            f"/api/v1/chapters/{chapter.chapter_uuid}/review/characters/{alice_uuid}",
            json={
                "expected_run_generation": gen,
                "expected_review_revision": rev,
                "canonical_name": "Alicia",
            },
        )
        assert ren_res.status_code == 200
        ren_data = ren_res.json()
        assert any(c["canonical_name"] == "Alicia" for c in ren_data["characters"])
        rev = ren_data["review_revision"]

        # Reassign speaker
        reassign_res = await client.post(
            f"/api/v1/chapters/{chapter.chapter_uuid}/review/speakers",
            json={
                "expected_run_generation": gen,
                "expected_review_revision": rev,
                "segment_uuids": [segs[0].segment_uuid],
                "target_character_uuid": alice_uuid,
            },
        )
        assert reassign_res.status_code == 200
        reassign_data = reassign_res.json()
        assert reassign_data["segments"][0]["character_uuid"] == alice_uuid
        rev = reassign_data["review_revision"]

        # Direct voice update returns 409 REOPEN_REVIEW_REQUIRED
        voice_res = await client.patch(
            f"/api/v1/characters/{alice_uuid}/voice",
            json={
                "voice_instruct": "New voice instruct",
                "current_chapter_uuid": chapter.chapter_uuid,
                "current_playback_index": 0,
            },
        )
        assert voice_res.status_code == 409
        assert voice_res.json()["code"] == "REOPEN_REVIEW_REQUIRED"

        # Request candidate preview
        prev_res = await client.post(
            f"/api/v1/chapters/{chapter.chapter_uuid}/review/characters/{alice_uuid}/voice-previews",
            json={
                "expected_run_generation": gen,
                "expected_review_revision": rev,
                "voice_instruct": "A warm melodious reading voice with natural pauses.",
            },
        )
        assert prev_res.status_code == 202
        prev_data = prev_res.json()
        assert "candidate_uuid" in prev_data

        # Approve review
        appr_res = await client.post(
            f"/api/v1/chapters/{chapter.chapter_uuid}/review/approve",
            json={
                "expected_run_generation": gen,
                "expected_review_revision": rev,
            },
        )
        assert appr_res.status_code == 200
        appr_data = appr_res.json()
        assert appr_data["review_phase"] == "SYNTHESIZING"
        assert appr_data["narration_authorized"] is True

        # Duplicate approval is idempotent
        dup_res = await client.post(
            f"/api/v1/chapters/{chapter.chapter_uuid}/review/approve",
            json={
                "expected_run_generation": gen,
                "expected_review_revision": rev,
            },
        )
        assert dup_res.status_code == 200
        assert dup_res.json()["review_phase"] == "SYNTHESIZING"

        # Return to review (reopen)
        reopen_res = await client.post(
            f"/api/v1/chapters/{chapter.chapter_uuid}/review/reopen",
            json={
                "expected_run_generation": gen,
                "expected_review_revision": rev,
            },
        )
        assert reopen_res.status_code == 200
        reopen_data = reopen_res.json()
        assert reopen_data["review_phase"] == "AWAITING_APPROVAL"
        assert reopen_data["narration_authorized"] is False
        assert reopen_data["run_generation"] == gen + 1

        # Serving segment audio after reopen returns 409 or 404
        seg_audio_res = await client.get(f"/api/v1/audio/segments/{segs[0].segment_uuid}.mp3")
        assert seg_audio_res.status_code in (404, 409)

