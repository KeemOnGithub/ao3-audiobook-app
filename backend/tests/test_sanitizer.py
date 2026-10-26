"""Unit tests for DOM Parsing, Story Isolation, and Content Sanitization."""

from bs4 import BeautifulSoup
import pytest

from backend.app.ingestion.exceptions import AO3ParsingError
from backend.app.ingestion.sanitizer import (
    build_normalized_paragraphs,
    extract_chapter_metadata,
    locate_story_container,
    normalize_text_glyphs,
    sanitize_story_dom,
)
from backend.tests.fixtures import (
    HTML_STANDARD_MULTI_CHAPTER,
    HTML_ONESHOT_WORK,
    HTML_WITH_FORMATTING_AND_SCENE_BREAKS,
)


def test_dom_isolation_multi_chapter():
    """Verify that author notes, work summaries, and end notes are strictly excluded."""
    soup = BeautifulSoup(HTML_STANDARD_MULTI_CHAPTER, "lxml")
    container = locate_story_container(soup)
    raw_blocks = sanitize_story_dom(container)
    paragraphs = build_normalized_paragraphs(raw_blocks)

    full_text = " ".join(p.text for p in paragraphs)

    # Assert notes and summaries are NOT present
    assert "Work-level author notes" not in full_text
    assert "Work-level summary" not in full_text
    assert "Chapter summary that should also be decomposed" not in full_text
    assert "Chapter beginning note" not in full_text
    assert "End notes from the author" not in full_text
    assert "Chapter Text" not in full_text  # landmark heading decomposed

    # Assert actual story narrative IS present
    assert "The morning light broke through" in full_text
    assert "Are you certain we are on the right path?" in full_text
    assert "Certainty is a luxury we cannot afford" in full_text
    assert "Later that evening, after the storm had cleared" in full_text

    # Verify scene break detected between Hero/Villain dialogue and riverbank camp
    assert len(paragraphs) == 4
    assert paragraphs[2].scene_break_after is True
    assert paragraphs[3].scene_break_after is False


def test_metadata_extraction_multi_chapter():
    """Verify rich metadata extraction from multi-chapter work."""
    soup = BeautifulSoup(HTML_STANDARD_MULTI_CHAPTER, "lxml")
    metadata, resolved_ch_id = extract_chapter_metadata(soup, "12345", None)

    assert resolved_ch_id == "987654"
    assert metadata.work_id == "12345"
    assert metadata.chapter_id == "987654"
    assert metadata.title == "The Great Adventure"
    assert metadata.author == "TestAuthor"
    assert metadata.authors == ["TestAuthor"]
    assert metadata.fandoms == ["Anime"]
    assert metadata.canonical_characters == ["Hero", "Villain"]
    assert metadata.relationship_tags == ["Hero/Villain"]
    assert metadata.language == "English"
    assert metadata.chapter_index == 1
    assert metadata.total_chapters == 5
    assert metadata.chapter_title == "The Beginning"
    assert metadata.next_chapter_url == "https://archiveofourown.org/works/12345/chapters/987655"
    assert metadata.prev_chapter_url is None


def test_metadata_extraction_oneshot():
    """Verify oneshot metadata resolution and single chapter detection."""
    soup = BeautifulSoup(HTML_ONESHOT_WORK, "lxml")
    metadata, resolved_ch_id = extract_chapter_metadata(soup, "778899", None)

    assert resolved_ch_id == "oneshot"
    assert metadata.chapter_id == "oneshot"
    assert metadata.canonical_url == "https://archiveofourown.org/works/778899"
    assert metadata.title == "A Quiet Night"
    assert metadata.author == "SoloWriter"
    assert metadata.chapter_title == "A Quiet Night"
    assert metadata.total_chapters == 1

    container = locate_story_container(soup)
    raw_blocks = sanitize_story_dom(container)
    paragraphs = build_normalized_paragraphs(raw_blocks)
    assert len(paragraphs) == 2


def test_formatting_markdown_and_glyph_normalization():
    """Verify inline formatting conversion (*, **) and Unicode/smart glyph normalization."""
    soup = BeautifulSoup(HTML_WITH_FORMATTING_AND_SCENE_BREAKS, "lxml")
    container = locate_story_container(soup)
    raw_blocks = sanitize_story_dom(container)
    paragraphs = build_normalized_paragraphs(raw_blocks)

    # 1. Inline formatting
    p0_text = paragraphs[0].text
    assert "*italic text*" in p0_text
    assert "**bold text**" in p0_text
    assert "***bold italic***" in p0_text or "**bold italic**" in p0_text or "*bold italic*" in p0_text

    # 2. Smart quotes and em dash normalization
    p1_text = paragraphs[1].text
    assert '"Wait—don\'t leave yet..."' in p1_text or '"Wait—don\'t leave yet..."' in p1_text
    assert "“" not in p1_text
    assert "”" not in p1_text
    assert "…" not in p1_text
    assert "..." in p1_text

    # 3. Zero-width and soft hyphen stripping
    p2_text = paragraphs[2].text
    assert "Zerowidthcharactersandsofthyphens here." in p2_text or "Zerowidthcharactersandsofthyphens" in p2_text
    assert "\u200b" not in p2_text
    assert "\u00ad" not in p2_text

    # 4. Legacy <br><br> split verification
    all_texts = [p.text for p in paragraphs]
    assert any("Legacy paragraph 1" in t for t in all_texts)
    assert any("Legacy paragraph 2" in t for t in all_texts)

    # 5. Scene break collapse: <hr> followed by <p>---</p> should collapse to a single break
    scene_break_count = sum(1 for p in paragraphs if p.scene_break_after)
    # Between Legacy paragraph 2 and Final closing thought, there was an hr and a --- which collapse
    assert scene_break_count == 2  # one from * * *, one from hr/---


def test_missing_container_raises_parsing_error():
    """Verify that an HTML document with missing story container raises AO3ParsingError."""
    bad_soup = BeautifulSoup("<div><p>Random page without story container</p></div>", "lxml")
    with pytest.raises(AO3ParsingError):
        locate_story_container(bad_soup)

