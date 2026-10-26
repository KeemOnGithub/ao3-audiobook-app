"""DOM Parsing, Story Sanitization, and Text Normalization for AO3."""

import re
import unicodedata
from typing import List, Optional, Tuple
from bs4 import BeautifulSoup, NavigableString, Tag

from backend.app.ingestion.exceptions import AO3ParsingError
from backend.app.ingestion.schemas import ChapterMetadata, NormalizedParagraph

SCENE_BREAK_REGEX = re.compile(r"^[\*\-\~\_\=\•\✦\❖\s]{3,}$")
CONSECUTIVE_BR_REGEX = re.compile(r"(?:<br\s*/?>[\s\u00a0]*){2,}", re.IGNORECASE)
SINGLE_BR_REGEX = re.compile(r"<br\s*/?>", re.IGNORECASE)

PUNCTUATION_TRANSLATION = str.maketrans({
    "\u2018": "'",  # Left single quote
    "\u2019": "'",  # Right single quote
    "\u201b": "'",  # Single high-reversed-9 quote
    "\u201c": '"',  # Left double quote
    "\u201d": '"',  # Right double quote
    "\u201f": '"',  # Double high-reversed-9 quote
    "\u00ab": '"',  # Left-pointing double angle quote
    "\u00bb": '"',  # Right-pointing double angle quote
    "\u2013": "—",  # En dash -> Em dash
    "\u2014": "—",  # Em dash
    "\u2015": "—",  # Horizontal bar -> Em dash
    "\u00a0": " ",  # Non-breaking space -> regular space
})

ZERO_WIDTH_CHARS = re.compile(r"[\u200b\u200c\u200d\ufeff\u00ad]")
HORIZONTAL_SPACES = re.compile(r"[ \t]+")


def normalize_text_glyphs(text: str) -> str:
    """Apply NFKC unicode normalization, remove invisible glyphs, and standardize quotes/dashes."""
    if not text:
        return ""
    # NFKC Normalization
    norm = unicodedata.normalize("NFKC", text)
    # Strip zero-width & soft hyphens
    norm = ZERO_WIDTH_CHARS.sub("", norm)
    # Replace ellipsis glyph
    norm = norm.replace("\u2026", "...")
    # Map quotes, dashes, NBSP
    norm = norm.translate(PUNCTUATION_TRANSLATION)
    # Collapse multiple horizontal spaces and tabs
    norm = HORIZONTAL_SPACES.sub(" ", norm)
    return norm.strip()


def extract_chapter_metadata(
    soup: BeautifulSoup,
    work_id: str,
    raw_chapter_id: Optional[str] = None
) -> Tuple[ChapterMetadata, str]:
    """
    Extract rich metadata and resolve canonical chapter_id from AO3 DOM.

    Returns:
        Tuple of (ChapterMetadata, resolved_chapter_id)
    """
    # 1. Resolve chapter_id
    resolved_chapter_id = raw_chapter_id
    if not resolved_chapter_id:
        select_tag = soup.find("select", id="selected_id")
        if select_tag:
            selected_option = select_tag.find("option", selected=True)
            if selected_option and selected_option.get("value"):
                resolved_chapter_id = selected_option["value"]
        if not resolved_chapter_id:
            chapter_index_ul = soup.find("ul", id="chapter_index")
            if chapter_index_ul:
                first_link = chapter_index_ul.find("a", href=re.compile(r"/chapters/(\d+)"))
                if first_link and first_link.get("href"):
                    m = re.search(r"/chapters/(\d+)", first_link["href"])
                    if m:
                        resolved_chapter_id = m.group(1)
        if not resolved_chapter_id:
            resolved_chapter_id = "oneshot"

    is_oneshot = resolved_chapter_id == "oneshot"
    if is_oneshot:
        canonical_url = f"https://archiveofourown.org/works/{work_id}"
    else:
        canonical_url = f"https://archiveofourown.org/works/{work_id}/chapters/{resolved_chapter_id}"

    # 2. Story Title
    title_tag = soup.select_one("div#workskin h2.title.heading")
    title = title_tag.get_text(strip=True) if title_tag else "Untitled"

    # 3. Authors
    author_tags = soup.select('div#workskin h3.byline.heading a[rel="author"]')
    authors = [a.get_text(strip=True) for a in author_tags if a.get_text(strip=True)]
    if not authors:
        authors = ["Anonymous"]
    author = ", ".join(authors)

    # 4. Tags: fandoms, characters, relationships, language
    fandom_tags = soup.select("dd.fandom.tags ul.commas li a.tag")
    fandoms = [f.get_text(strip=True) for f in fandom_tags if f.get_text(strip=True)]

    char_tags = soup.select("dd.character.tags ul.commas li a.tag")
    canonical_characters = [c.get_text(strip=True) for c in char_tags if c.get_text(strip=True)]

    rel_tags = soup.select("dd.relationship.tags ul.commas li a.tag")
    relationship_tags = [r.get_text(strip=True) for r in rel_tags if r.get_text(strip=True)]

    lang_tag = soup.select_one("dd.language")
    language = lang_tag.get_text(strip=True) if lang_tag else "English"

    # 5. Chapter Index and Total Chapters
    chapter_index = 1
    select_tag = soup.find("select", id="selected_id")
    if select_tag:
        options = select_tag.find_all("option")
        for idx, opt in enumerate(options, start=1):
            if opt.has_attr("selected"):
                chapter_index = idx
                break

    total_chapters: Optional[int] = None
    chapters_dd = soup.select_one("dd.chapters")
    if chapters_dd:
        ch_text = chapters_dd.get_text(strip=True)
        # Format "X/Y" or "X/?"
        if "/" in ch_text:
            _, denom = ch_text.split("/", 1)
            if denom.strip().isdigit():
                total_chapters = int(denom.strip())

    # 6. Chapter Title
    chapter_heading = soup.select_one("div#chapters div.chapter.preface.group h3.title")
    if chapter_heading:
        raw_ch_title = chapter_heading.get_text(strip=True)
        # Strip leading "Chapter X: " prefix if present or retain cleaned text
        ch_title = re.sub(r"^Chapter\s+\d+:\s*", "", raw_ch_title).strip()
        if not ch_title:
            ch_title = raw_ch_title
    else:
        ch_title = title if is_oneshot else f"Chapter {chapter_index}"

    # 7. Next / Prev navigation links
    next_link = soup.select_one("li.chapter.next a")
    next_chapter_url: Optional[str] = None
    if next_link and next_link.get("href"):
        href = next_link["href"]
        next_chapter_url = f"https://archiveofourown.org{href}" if href.startswith("/") else href

    prev_link = soup.select_one("li.chapter.previous a")
    prev_chapter_url: Optional[str] = None
    if prev_link and prev_link.get("href"):
        href = prev_link["href"]
        prev_chapter_url = f"https://archiveofourown.org{href}" if href.startswith("/") else href

    metadata = ChapterMetadata(
        work_id=work_id,
        chapter_id=resolved_chapter_id,
        canonical_url=canonical_url,
        title=title,
        author=author,
        authors=authors,
        fandoms=fandoms,
        canonical_characters=canonical_characters,
        relationship_tags=relationship_tags,
        language=language,
        chapter_index=chapter_index,
        total_chapters=total_chapters,
        chapter_title=ch_title,
        next_chapter_url=next_chapter_url,
        prev_chapter_url=prev_chapter_url,
        total_words=0,
        total_paragraphs=0,
        total_chunks=0,
    )

    return metadata, resolved_chapter_id


def locate_story_container(soup: BeautifulSoup) -> Tag:
    """
    Locate the exact story prose container in DOM using the Spec 01 fallback cascade.
    Guarantees chapter summaries and author notes are not included.
    """
    container = soup.select_one('div#chapters div.userstuff.module[role="article"]')
    if container:
        return container

    container = soup.select_one('div#chapters > div.userstuff')
    if container:
        return container

    container = soup.select_one('div#workskin div.userstuff')
    if container:
        return container

    raise AO3ParsingError("Could not locate story prose container in DOM.")


def transform_inline_formatting(tag: Tag) -> None:
    """
    Bottom-up conversion of <em>, <i>, <strong>, <b> to markdown *italic* and **bold**.
    Preserves leading and trailing whitespace outside markdown delimiters.
    """
    # Find all inline styling tags in reverse order (innermost first)
    inline_tags = tag.find_all(["em", "i", "strong", "b"])
    for elem in reversed(inline_tags):
        if not elem.parent:
            continue
        inner = elem.get_text()
        stripped_inner = inner.strip()
        if not stripped_inner:
            elem.decompose()
            continue

        prefix = " " if inner.startswith(" ") or inner.startswith("\t") else ""
        suffix = " " if inner.endswith(" ") or inner.endswith("\t") else ""

        if elem.name in ("em", "i"):
            replacement = f"{prefix}*{stripped_inner}*{suffix}"
        else:  # strong or b
            replacement = f"{prefix}**{stripped_inner}**{suffix}"

        elem.replace_with(NavigableString(replacement))


def sanitize_story_dom(container: Tag) -> List[Tuple[str, bool]]:
    """
    Decompose non-narrative elements, normalize formatting and scene breaks,
    and return list of raw paragraph strings and scene_break flags:
    List of (paragraph_text: str, is_scene_break: bool)
    """
    # 1. Strip non-narrative elements
    for landmark in container.find_all("h3", class_="landmark heading"):
        landmark.decompose()

    for script in container.find_all(["script", "style", "noscript"]):
        script.decompose()

    for note in container.find_all("div", class_=lambda c: c and any(cls in c for cls in ["notes", "summary", "end"])):
        note.decompose()

    # 2. Convert inline formatting to markdown (em, i, strong, b)
    transform_inline_formatting(container)

    # 3. Process blocks (p, blockquote, h1-h6, li, hr) and handle <br><br>
    raw_blocks: List[Tuple[str, bool]] = []

    # Iterate over direct child elements or top-level flow
    children = list(container.children)
    for child in children:
        if isinstance(child, NavigableString):
            text = child.strip()
            if text:
                raw_blocks.append((text, False))
            continue

        if not isinstance(child, Tag):
            continue

        # Check for <hr>
        if child.name == "hr":
            raw_blocks.append(("[SCENE_BREAK]", True))
            continue

        # Handle block tags
        if child.name in ["p", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "li", "div"]:
            # Check for <br><br> legacy splits inside this block
            block_html = str(child)
            if CONSECUTIVE_BR_REGEX.search(block_html):
                # Split by consecutive br tags
                sub_parts = CONSECUTIVE_BR_REGEX.split(block_html)
                for part in sub_parts:
                    sub_soup = BeautifulSoup(part, "lxml")
                    # Replace single <br> with space/newline
                    for br in sub_soup.find_all("br"):
                        br.replace_with(NavigableString(" "))
                    part_text = sub_soup.get_text()
                    if part_text.strip():
                        raw_blocks.append((part_text, False))
                continue

            # Replace single <br> with a space
            for br in child.find_all("br"):
                br.replace_with(NavigableString(" "))

            child_text = child.get_text()
            stripped = child_text.strip()
            if not stripped:
                continue

            # Check if stripped text is purely a scene break glyph string
            if SCENE_BREAK_REGEX.match(stripped):
                raw_blocks.append(("[SCENE_BREAK]", True))
            else:
                raw_blocks.append((child_text, False))

    return raw_blocks


def build_normalized_paragraphs(raw_blocks: List[Tuple[str, bool]]) -> List[NormalizedParagraph]:
    """
    Clean text with glyph normalization, collapse and attach scene breaks to preceding paragraphs.
    """
    paragraphs: List[NormalizedParagraph] = []
    pending_scene_break = False

    for text, is_sb in raw_blocks:
        if is_sb or text == "[SCENE_BREAK]":
            # Only set scene break if there is already a preceding paragraph
            if paragraphs:
                pending_scene_break = True
            continue

        cleaned_text = normalize_text_glyphs(text)
        if not cleaned_text:
            continue

        words = len(cleaned_text.split())
        if words == 0:
            continue

        # If previous paragraph had pending_scene_break, attach it to the previous paragraph
        if pending_scene_break and paragraphs:
            # We recreate the previous paragraph with scene_break_after = True
            prev = paragraphs[-1]
            paragraphs[-1] = NormalizedParagraph(
                paragraph_index=prev.paragraph_index,
                text=prev.text,
                word_count=prev.word_count,
                scene_break_after=True,
            )
            pending_scene_break = False

        paragraphs.append(
            NormalizedParagraph(
                paragraph_index=len(paragraphs),
                text=cleaned_text,
                word_count=words,
                scene_break_after=False,
            )
        )

    # Note: Trailing scene break at end of chapter is intentionally discarded (pending_scene_break is dropped)
    return paragraphs

