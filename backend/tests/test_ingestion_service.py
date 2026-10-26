"""Integration tests for Ingestion Service pipeline."""

import pytest

from backend.app.ingestion.schemas import IngestionResult
from backend.app.ingestion.service import ingest_ao3_chapter
from backend.tests.fixtures import (
    HTML_STANDARD_MULTI_CHAPTER,
    HTML_ONESHOT_WORK,
    HTML_WITH_FORMATTING_AND_SCENE_BREAKS,
)


@pytest.mark.asyncio
async def test_ingest_multi_chapter_offline():
    """Verify full end-to-end ingestion pipeline on multi-chapter HTML."""
    url = "https://archiveofourown.org/works/12345/chapters/987654"
    result: IngestionResult = await ingest_ao3_chapter(url, html_override=HTML_STANDARD_MULTI_CHAPTER)

    assert result.metadata.work_id == "12345"
    assert result.metadata.chapter_id == "987654"
    assert result.metadata.title == "The Great Adventure"
    assert result.metadata.author == "TestAuthor"
    assert result.metadata.chapter_index == 1
    assert result.metadata.total_chapters == 5
    assert result.metadata.total_paragraphs == 4
    assert result.metadata.total_chunks == 1
    assert result.metadata.total_words > 0

    assert len(result.chunks) == 1
    chunk = result.chunks[0]
    assert chunk.chunk_index == 0
    assert chunk.is_fast_start is True
    assert chunk.is_last_chunk is True
    assert chunk.start_paragraph_index == 0
    assert chunk.end_paragraph_index == 3
    assert "[SCENE_BREAK]" in chunk.formatted_text


@pytest.mark.asyncio
async def test_ingest_oneshot_work_root_offline():
    """Verify single round-trip resolution for oneshot work root URL."""
    url = "https://archiveofourown.org/works/778899"
    result: IngestionResult = await ingest_ao3_chapter(url, html_override=HTML_ONESHOT_WORK)

    assert result.metadata.work_id == "778899"
    assert result.metadata.chapter_id == "oneshot"
    assert result.metadata.canonical_url == "https://archiveofourown.org/works/778899"
    assert result.metadata.title == "A Quiet Night"
    assert result.metadata.total_paragraphs == 2
    assert result.metadata.total_chunks == 1


@pytest.mark.asyncio
async def test_ingest_formatted_work_offline():
    """Verify end-to-end sanitization of formatting, glyphs, and legacy br breaks."""
    url = "https://archiveofourown.org/works/443322"
    result: IngestionResult = await ingest_ao3_chapter(url, html_override=HTML_WITH_FORMATTING_AND_SCENE_BREAKS)

    assert result.metadata.work_id == "443322"
    assert len(result.chunks) >= 1
    chunk_text = result.chunks[0].formatted_text

    # Check preserved markup
    assert "*italic text*" in chunk_text
    assert "**bold text**" in chunk_text
    assert "“" not in chunk_text
    assert "”" not in chunk_text
    assert "—" in chunk_text  # em-dash
    assert "[SCENE_BREAK]" in chunk_text

