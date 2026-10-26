"""Narrative Chunking Algorithm per Spec 01 section 5."""

import re
from typing import List
from backend.app.ingestion.schemas import NarrativeChunk, NormalizedParagraph

SENTENCE_SPLIT_REGEX = re.compile(r'(?<=[.!?])\s+|(?<=[.!?]["\'\*])\s+')


def count_words(text: str) -> int:
    """Whitespace-delimited word count excluding [SCENE_BREAK] tokens."""
    cleaned = text.replace("[SCENE_BREAK]", " ")
    return len(cleaned.split())


def format_chunk_text(paragraphs: List[NormalizedParagraph]) -> str:
    """
    Format paragraphs into a cohesive narrative string:
    Joined by '\\n\\n' with '\\n\\n[SCENE_BREAK]\\n\\n' where scene_break_after is True.
    """
    parts: List[str] = []
    for i, p in enumerate(paragraphs):
        parts.append(p.text)
        # If scene break follows and this is not the last paragraph (or even if it is within the chunk)
        if p.scene_break_after:
            parts.append("[SCENE_BREAK]")
    return "\n\n".join(parts)


def split_oversized_paragraph(
    para: NormalizedParagraph,
    target_max_words: int
) -> List[NormalizedParagraph]:
    """
    Split an oversized paragraph along sentence boundaries into sub-paragraphs
    each having <= target_max_words, preserving paragraph_index.
    """
    sentences = SENTENCE_SPLIT_REGEX.split(para.text)
    sub_paragraphs: List[NormalizedParagraph] = []
    current_sentences: List[str] = []
    current_words = 0

    for s in sentences:
        s_clean = s.strip()
        if not s_clean:
            continue
        s_words = count_words(s_clean)
        if current_sentences and (current_words + s_words > target_max_words):
            sub_text = " ".join(current_sentences)
            sub_paragraphs.append(
                NormalizedParagraph(
                    paragraph_index=para.paragraph_index,
                    text=sub_text,
                    word_count=count_words(sub_text),
                    scene_break_after=False,
                )
            )
            current_sentences = [s_clean]
            current_words = s_words
        else:
            current_sentences.append(s_clean)
            current_words += s_words

    if current_sentences:
        sub_text = " ".join(current_sentences)
        sub_paragraphs.append(
            NormalizedParagraph(
                paragraph_index=para.paragraph_index,
                text=sub_text,
                word_count=count_words(sub_text),
                scene_break_after=para.scene_break_after,
            )
        )

    return sub_paragraphs if sub_paragraphs else [para]


def partition_into_chunks(paragraphs: List[NormalizedParagraph]) -> List[NarrativeChunk]:
    """
    Greedy narrative chunking partition algorithm per Spec 01:
    - Chunk 0: target 350-500 words, hard max 650
    - Chunks 1..N: target 800-1200 words, hard max 1400
    - Prior context: 2 paragraphs from prior chunk
    - Tail merging: merge tail < 150 words into preceding chunk if within hard max
    """
    if not paragraphs:
        return []

    total_words = sum(p.word_count for p in paragraphs)
    # Edge case: entire chapter < 350 words -> single Chunk 0
    if total_words < 350:
        return [
            NarrativeChunk(
                chunk_index=0,
                is_fast_start=True,
                is_last_chunk=True,
                word_count=total_words,
                start_paragraph_index=paragraphs[0].paragraph_index,
                end_paragraph_index=paragraphs[-1].paragraph_index,
                read_only_prior_context=[],
                paragraphs=paragraphs,
                formatted_text=format_chunk_text(paragraphs),
            )
        ]

    chunks_paragraphs: List[List[NormalizedParagraph]] = []
    current_paras: List[NormalizedParagraph] = []
    current_words = 0

    def current_bounds() -> tuple[int, int, int]:
        """Return (target_min, target_max, hard_max) based on whether building Chunk 0 or 1+."""
        if len(chunks_paragraphs) == 0:
            return (350, 500, 650)
        return (800, 1200, 1400)

    for para in paragraphs:
        target_min, target_max, hard_max = current_bounds()

        # Handle oversized paragraph
        if para.word_count > hard_max:
            sub_paras = split_oversized_paragraph(para, target_max)
        else:
            sub_paras = [para]

        for p in sub_paras:
            target_min, target_max, hard_max = current_bounds()

            # Scene-break preference check:
            # If current_words >= target_min_words and the previous paragraph had scene_break_after
            if current_paras and current_words >= target_min and current_paras[-1].scene_break_after:
                chunks_paragraphs.append(current_paras)
                current_paras = []
                current_words = 0
                target_min, target_max, hard_max = current_bounds()

            # Upper-bound check
            if current_words + p.word_count <= target_max:
                current_paras.append(p)
                current_words += p.word_count
            elif current_words < target_min and (current_words + p.word_count <= hard_max):
                current_paras.append(p)
                current_words += p.word_count
                chunks_paragraphs.append(current_paras)
                current_paras = []
                current_words = 0
            else:
                if current_paras:
                    chunks_paragraphs.append(current_paras)
                current_paras = [p]
                current_words = p.word_count

    if current_paras:
        chunks_paragraphs.append(current_paras)

    # Tail remainder merging:
    # If final chunk has < 150 words and len(chunks_paragraphs) >= 2 (or 1):
    if len(chunks_paragraphs) >= 2:
        last_chunk_paras = chunks_paragraphs[-1]
        last_words = sum(p.word_count for p in last_chunk_paras)
        if last_words < 150:
            prev_paras = chunks_paragraphs[-2]
            prev_words = sum(p.word_count for p in prev_paras)
            prev_hard_max = 650 if len(chunks_paragraphs) == 2 else 1400
            if prev_words + last_words <= prev_hard_max:
                chunks_paragraphs[-2].extend(last_chunk_paras)
                chunks_paragraphs.pop()

    # Build final NarrativeChunk objects with read_only_prior_context
    result_chunks: List[NarrativeChunk] = []
    num_chunks = len(chunks_paragraphs)

    for idx, paras in enumerate(chunks_paragraphs):
        chunk_words = sum(p.word_count for p in paras)
        is_fast_start = (idx == 0)
        is_last = (idx == num_chunks - 1)

        prior_context: List[NormalizedParagraph] = []
        if idx > 0:
            # Last 2 paragraphs from prior chunk
            prior_chunk_paras = chunks_paragraphs[idx - 1]
            prior_context = prior_chunk_paras[-2:] if len(prior_chunk_paras) >= 2 else prior_chunk_paras[:]

        result_chunks.append(
            NarrativeChunk(
                chunk_index=idx,
                is_fast_start=is_fast_start,
                is_last_chunk=is_last,
                word_count=chunk_words,
                start_paragraph_index=paras[0].paragraph_index,
                end_paragraph_index=paras[-1].paragraph_index,
                read_only_prior_context=prior_context,
                paragraphs=paras,
                formatted_text=format_chunk_text(paras),
            )
        )

    return result_chunks
