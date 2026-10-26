"""Orchestration service for AO3 chapter ingestion & normalization."""

from typing import Optional
from bs4 import BeautifulSoup

from backend.app.ingestion.chunker import partition_into_chunks
from backend.app.ingestion.client import AO3HttpClient
from backend.app.ingestion.sanitizer import (
    build_normalized_paragraphs,
    extract_chapter_metadata,
    locate_story_container,
    sanitize_story_dom,
)
from backend.app.ingestion.schemas import IngestionResult
from backend.app.ingestion.url_parser import build_fetch_url, parse_ao3_url


async def ingest_ao3_chapter(
    raw_url: str,
    html_override: Optional[str] = None,
    http_client: Optional[AO3HttpClient] = None,
) -> IngestionResult:
    """
    Main pipeline entrypoint for Phase 1 ingestion:
    1. Parse and validate AO3 URL.
    2. Fetch chapter HTML (or use offline html_override).
    3. Extract metadata and resolve chapter ID.
    4. Isolate story container and sanitize markup.
    5. Construct sequential NormalizedParagraphs.
    6. Partition into LLM-ready NarrativeChunks.
    """
    work_id, chapter_id = parse_ao3_url(raw_url)

    if html_override is not None:
        html_text = html_override
    else:
        canonical_fetch_url = build_fetch_url(work_id, chapter_id)
        client = http_client or AO3HttpClient()
        html_text = await client.fetch_html(canonical_fetch_url)

    # Parse DOM
    soup = BeautifulSoup(html_text, "lxml")

    # Extract metadata and resolve canonical chapter_id (if work root was passed)
    metadata, resolved_chapter_id = extract_chapter_metadata(soup, work_id, chapter_id)

    # Locate narrative container
    container = locate_story_container(soup)

    # Sanitize and extract raw blocks
    raw_blocks = sanitize_story_dom(container)

    # Build normalized paragraphs
    paragraphs = build_normalized_paragraphs(raw_blocks)

    # Partition paragraphs into narrative chunks
    chunks = partition_into_chunks(paragraphs)

    # Finalize metadata totals
    total_words = sum(p.word_count for p in paragraphs)
    total_paragraphs = len(paragraphs)
    total_chunks = len(chunks)

    final_metadata = metadata.model_copy(
        update={
            "chapter_id": resolved_chapter_id,
            "total_words": total_words,
            "total_paragraphs": total_paragraphs,
            "total_chunks": total_chunks,
        }
    )

    return IngestionResult(metadata=final_metadata, chunks=chunks)

