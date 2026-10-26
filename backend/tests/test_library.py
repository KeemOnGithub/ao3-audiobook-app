"""Saved chapter library repository and read-only API coverage."""

from dataclasses import asdict
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from backend.app.api.routes import router
from backend.app.db.connection import DatabaseManager
from backend.app.db.repository import AudiobookRepository


@pytest.fixture
async def library_env(tmp_path):
    manager = DatabaseManager(str(tmp_path / "library.db"))
    await manager.init_db()
    repo = AudiobookRepository(manager)
    app = FastAPI()
    app.include_router(router)
    app.state.repository = repo
    app.state.orchestrator = AsyncMock()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield repo, manager, app, client


@pytest.mark.asyncio
async def test_empty_library(library_env):
    repo, _, _, client = library_env
    assert await repo.get_saved_chapter_library() == []
    response = await client.get("/api/v1/library")
    assert response.status_code == 200
    assert response.json() == {"works": []}


@pytest.mark.asyncio
async def test_library_order_lifecycle_availability_and_read_only(library_env, tmp_path):
    repo, manager, app, client = library_env
    audio = tmp_path / "master.mp3"
    audio.write_bytes(b"ID3")
    chapters = []
    for work_id, title in [("z", "Other"), ("b", "Duplicate"), ("a", "Duplicate")]:
        await repo.upsert_work(work_id, title, "Author " + work_id, [])
        for chapter_id, number, chapter_status, path in [
            ("ten", 10, "COMPLETED", str(audio)),
            ("two", 2, "COMPLETED", str(tmp_path / "missing.mp3")),
            ("tie", 2, "COMPLETED", str(tmp_path)),
            ("null", 3, "COMPLETED", None),
            ("pending", 4, "PENDING", None),
            ("streaming", 5, "STREAMING", None),
            ("mastering", 6, "MASTERING", None),
            ("failed", 7, "FAILED", None),
        ]:
            chapter = await repo.upsert_chapter(
                work_id, chapter_id, number, "Chapter " + chapter_id,
                f"https://archiveofourown.org/works/{work_id}/chapters/{chapter_id}",
            )
            async with manager.get_connection() as conn:
                await conn.execute(
                    """UPDATE chapters SET status=?,master_mp3_path=?,review_phase=?,
                    approved_at='fixture',analysis_completed_at='fixture',approved_run_generation=run_generation,
                    approved_review_revision=review_revision WHERE chapter_uuid=?""",
                    (chapter_status,path,{'PENDING':'AWAITING_APPROVAL','STREAMING':'SYNTHESIZING'}.get(chapter_status,chapter_status),chapter.chapter_uuid),
                )
                await conn.commit()
            chapters.append(await repo.get_chapter_by_uuid(chapter.chapter_uuid))

    rows = await repo.get_saved_chapter_library()
    assert len(rows) == 24
    assert [(r.title, r.work_id, r.chapter_number, r.chapter_uuid) for r in rows] == sorted(
        (r.title, r.work_id, r.chapter_number, r.chapter_uuid) for r in rows
    )
    assert all(r.created_at and r.updated_at and r.work_created_at and r.work_updated_at for r in rows)
    assert all("segments" not in asdict(r) and "characters" not in asdict(r) for r in rows)

    response = await client.get("/api/v1/library")
    assert response.status_code == 200
    works = response.json()["works"]
    assert [w["work_id"] for w in works] == ["a", "b", "z"]
    for work in works:
        assert work["author"] == "Author " + work["work_id"]
        assert [c["chapter_number"] for c in work["chapters"]] == [2, 2, 3, 4, 5, 6, 7, 10]
        assert {c["status"] for c in work["chapters"]} == {"PENDING", "STREAMING", "MASTERING", "COMPLETED", "FAILED"}
        for chapter in work["chapters"]:
            assert chapter["master_audio_available"] == (chapter["chapter_id"] == "ten")
            assert set(chapter) == {
                "chapter_uuid", "chapter_id", "chapter_number", "chapter_title",
                "source_url", "status", "master_audio_available", "created_at", "updated_at",'review_phase',
            }
    assert str(tmp_path) not in response.text
    assert "master_mp3_path" not in response.text
    assert "text_content" not in response.text
    assert [await repo.get_chapter_by_uuid(c.chapter_uuid) for c in chapters] == chapters
    assert not app.state.orchestrator.mock_calls


@pytest.mark.asyncio
async def test_generation_replacement_retains_identity_and_superseded_chapters(library_env):
    repo, manager, _, client = library_env
    await repo.upsert_work("work", "Story", "Author", [])
    first = await repo.upsert_chapter("work", "one", 1, "First", "https://example.com/one")
    async with manager.get_connection() as conn:
        await conn.execute("UPDATE chapters SET status = 'COMPLETED' WHERE chapter_uuid = ?", (first.chapter_uuid,))
        await conn.commit()
    before = (await client.get("/api/v1/library")).json()["works"][0]["chapters"]
    assert before[0]["status"] == "COMPLETED"
    replaced = await repo.replace_chapter_generation(first.chapter_uuid, 2, total_chunks=3)
    assert replaced.chapter_uuid == first.chapter_uuid
    during = (await client.get("/api/v1/library")).json()["works"][0]["chapters"]
    assert len(during) == 1
    assert during[0]["chapter_uuid"] == first.chapter_uuid
    assert during[0]["status"] == "STREAMING"
    await repo.transition_chapter_failed(first.chapter_uuid, 2, "SUPERSEDED", "New chapter loaded")
    second = await repo.upsert_chapter("work", "two", 2, "Second", "https://example.com/two")
    after = (await client.get("/api/v1/library")).json()["works"][0]["chapters"]
    assert [(row["chapter_uuid"], row["status"]) for row in after] == [
        (first.chapter_uuid, "FAILED"), (second.chapter_uuid, "STREAMING"),
    ]


@pytest.mark.asyncio
async def test_library_database_failure_is_structured_and_private(library_env, monkeypatch):
    repo, _, _, client = library_env
    monkeypatch.setattr(repo, "get_saved_chapter_library", AsyncMock(
        side_effect=RuntimeError("private/database/path")
    ))
    response = await client.get("/api/v1/library")
    assert response.status_code == 500
    assert response.json() == {"detail": "Unable to retrieve chapter library."}
