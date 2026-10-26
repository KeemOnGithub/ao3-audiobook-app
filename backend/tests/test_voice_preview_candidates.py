"""Candidates remain separate from selected voices until atomic application."""
import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.tests.test_chapter_review import prepared
from backend.app.db.review_repository import ReviewConflict
from backend.app.orchestration.orchestrator import JobOrchestrator
from backend.app.orchestration.event_bus import ChapterEventBus


@pytest.mark.asyncio
async def test_unique_candidates_exact_application_and_stale_completion(tmp_path):
    _,repo,ch,narrator,_ = await prepared(tmp_path)
    one,two = await asyncio.gather(*[repo.create_voice_preview_candidate(ch.chapter_uuid,narrator.character_uuid,0,1,
        'An expressive warm narrator voice.',None) for _ in range(2)])
    assert one['candidate_uuid'] != two['candidate_uuid']
    for candidate in (one,two):
        assert await repo.update_voice_preview_candidate(candidate['candidate_uuid'],'READY',{
            'embedding_path':candidate['candidate_uuid']+'.pt','preview_audio_path':candidate['candidate_uuid']+'.mp3',
            'duration_ms':100,'artifact_provider':'simulation','artifact_fingerprint':'exact'})
    unchanged = await repo.get_character_by_uuid(narrator.character_uuid)
    assert unchanged.voice_version == 1 and unchanged.embedding_pt_path is None
    await repo.apply_voice_preview_candidate(ch.chapter_uuid,two['candidate_uuid'],0,1)
    applied = await repo.get_character_by_uuid(narrator.character_uuid)
    assert applied.voice_version == 2 and applied.embedding_pt_path == two['candidate_uuid']+'.pt'
    assert applied.preview_mp3_path == two['candidate_uuid']+'.mp3' and applied.artifact_fingerprint == 'exact'
    assert not await repo.update_voice_preview_candidate(one['candidate_uuid'],'READY',{'embedding_path':'late.pt'})
    with pytest.raises(ReviewConflict):
        await repo.apply_voice_preview_candidate(ch.chapter_uuid,one['candidate_uuid'],0,2)
    with pytest.raises(ReviewConflict):
        await repo.apply_voice_preview_candidate(ch.chapter_uuid,two['candidate_uuid'],0,2)


@pytest.mark.asyncio
async def test_preview_failure_and_approval_revoke_without_narration(tmp_path):
    _,repo,ch,narrator,_ = await prepared(tmp_path)
    calls = []
    def unavailable(request):
        calls.append(request.url.path)
        return httpx.Response(503,json={'detail':'Unavailable'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable)) as client:
        orch = JobOrchestrator(repo,ChapterEventBus(),AsyncMock(),tts_http_client=client)
        candidate = await repo.create_voice_preview_candidate(ch.chapter_uuid,narrator.character_uuid,0,1,
            'A warm expressive narrator voice.')
        await orch.generate_voice_preview(candidate)
        assert (await repo.get_voice_preview_candidate(ch.chapter_uuid,candidate['candidate_uuid']))['status'] == 'FAILED'
        assert (await repo.get_chapter_by_uuid(ch.chapter_uuid)).review_phase == 'AWAITING_APPROVAL'
        candidate = await repo.create_voice_preview_candidate(ch.chapter_uuid,narrator.character_uuid,0,1,
            'A different warm expressive voice.')
        await repo.approve_chapter_review(ch.chapter_uuid,0,1)
        assert not await repo.update_voice_preview_candidate(candidate['candidate_uuid'],'READY',{'embedding_path':'late'})
        assert calls == ['/internal/tts/create-voice']
