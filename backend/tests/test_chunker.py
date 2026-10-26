"""Unit tests for the Narrative Chunking Algorithm per Spec 01 section 7."""

import pytest
from backend.app.ingestion.chunker import (
    count_words,
    format_chunk_text,
    partition_into_chunks,
    split_oversized_paragraph,
)
from backend.app.ingestion.schemas import NormalizedParagraph


def make_paragraph(index: int, word_count: int, scene_break_after: bool = False) -> NormalizedParagraph:
    """Helper to generate dummy paragraph with precise word count."""
    words = [f"word{i}" for i in range(word_count)]
    text = " ".join(words) + "."
    return NormalizedParagraph(
        paragraph_index=index,
        text=text,
        word_count=word_count,
        scene_break_after=scene_break_after,
    )


def test_mini_chapter_under_350_words():
    """Verify 200-word mini-chapter produces exactly one Chunk 0 with is_last_chunk=True."""
    paragraphs = [
        make_paragraph(0, 100),
        make_paragraph(1, 100),
    ]
    chunks = partition_into_chunks(paragraphs)

    assert len(chunks) == 1
    c0 = chunks[0]
    assert c0.chunk_index == 0
    assert c0.is_fast_start is True
    assert c0.is_last_chunk is True
    assert c0.word_count == 200
    assert c0.start_paragraph_index == 0
    assert c0.end_paragraph_index == 1
    assert c0.read_only_prior_context == []


def test_standard_3500_word_chapter():
    """
    Verify 3,500-word chapter partitioning:
    - Chunk 0 in [350, 500] target words (hard max 650)
    - Subsequent chunks in [800, 1200] target words (hard max 1400)
    - 2-paragraph read_only_prior_context on Chunk 1+
    """
    # Create 35 paragraphs of 100 words each (3500 words total)
    paragraphs = [make_paragraph(i, 100) for i in range(35)]
    chunks = partition_into_chunks(paragraphs)

    assert len(chunks) >= 3

    # Check Chunk 0
    c0 = chunks[0]
    assert c0.chunk_index == 0
    assert c0.is_fast_start is True
    assert 350 <= c0.word_count <= 650
    assert c0.read_only_prior_context == []

    # Check Chunks 1+
    for c in chunks[1:]:
        assert c.is_fast_start is False
        assert len(c.read_only_prior_context) == 2
        # Verify read_only_prior_context matches the last 2 paragraphs of the previous chunk
        prev_chunk = chunks[c.chunk_index - 1]
        assert [p.paragraph_index for p in c.read_only_prior_context] == [
            p.paragraph_index for p in prev_chunk.paragraphs[-2:]
        ]
        assert c.word_count <= 1400

    # Final chunk marked is_last_chunk
    assert chunks[-1].is_last_chunk is True


def test_single_900_word_oversized_paragraph():
    """Verify single 900-word paragraph splits along sentence boundaries and respects bounds."""
    # Build a 900-word paragraph composed of 10-word sentences
    sentence = "The quick brown fox jumps over the lazy sleeping dog."
    assert len(sentence.split()) == 10
    full_text = " ".join([sentence for _ in range(90)])  # 900 words
    para = NormalizedParagraph(
        paragraph_index=0,
        text=full_text,
        word_count=count_words(full_text),
        scene_break_after=False,
    )

    chunks = partition_into_chunks([para])
    assert len(chunks) >= 2
    # Chunk 0 hard max is 650
    assert chunks[0].word_count <= 650
    # Both chunks retain paragraph_index = 0
    for chunk in chunks:
        for p in chunk.paragraphs:
            assert p.paragraph_index == 0


def test_trailing_60_word_paragraph_merging():
    """Verify 60-word trailing paragraph is merged into previous chunk instead of creating tiny orphan."""
    # 5 paragraphs of 100 words (500 words -> Chunk 0 target_max met)
    # Then 12 paragraphs of 100 words (1200 words -> Chunk 1 target_max met)
    # Then 1 trailing paragraph of 60 words (would otherwise start an orphan Chunk 2)
    paras = [make_paragraph(i, 100) for i in range(17)]
    paras.append(make_paragraph(17, 60))

    chunks = partition_into_chunks(paras)

    # There should only be 2 chunks because the 60-word remainder is merged into Chunk 1
    assert len(chunks) == 2
    assert chunks[0].chunk_index == 0
    assert chunks[0].word_count == 500

    assert chunks[1].chunk_index == 1
    assert chunks[1].end_paragraph_index == 17
    # 1200 + 60 = 1260 words (merged, and <= 1400 hard_max)
    assert chunks[1].word_count == 1260
    assert chunks[1].is_last_chunk is True


def test_scene_break_preference_split():
    """Verify chunks prefer splitting at scene breaks when target_min_words is satisfied."""
    # Chunk 0 target min is 350.
    # Paragraphs 0, 1, 2, 3 have 100 words each (400 words >= 350).
    # Paragraph 3 has scene_break_after=True.
    # Paragraph 4 has 100 words.
    p0 = make_paragraph(0, 100)
    p1 = make_paragraph(1, 100)
    p2 = make_paragraph(2, 100)
    p3 = make_paragraph(3, 100, scene_break_after=True)
    p4 = make_paragraph(4, 100)
    p5 = make_paragraph(5, 300)

    chunks = partition_into_chunks([p0, p1, p2, p3, p4, p5])
    assert len(chunks) >= 2
    # Chunk 0 should finalize at paragraph 3 (the scene break)
    assert chunks[0].end_paragraph_index == 3
    assert chunks[0].word_count == 400
    assert "[SCENE_BREAK]" in chunks[0].formatted_text
