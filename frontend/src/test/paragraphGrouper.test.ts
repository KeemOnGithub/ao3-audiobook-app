import { describe, it, expect } from 'vitest';
import { groupSegmentsIntoParagraphs } from '../utils/paragraphGrouper';
import { ScriptSegment } from '../types/domain';

function createMockSegment(
  order: number,
  isParaEnd: boolean,
  text: string,
  paraIndex = 0,
  isDialogue = false
): ScriptSegment {
  return {
    segment_uuid: `uuid_${order}`,
    segment_order: order,
    chunk_index: 0,
    paragraph_index: paraIndex,
    is_paragraph_end: isParaEnd,
    speaker_uuid: isDialogue ? 'char_hero' : 'char_narrator',
    speaker_name: isDialogue ? 'Hero' : 'Narrator',
    is_dialogue: isDialogue,
    text,
    audio_status: 'READY',
    audio_url: `/audio/${order}.mp3`,
    duration_ms: 1500,
    voice_version: 1,
  };
}

describe('paragraphGrouper', () => {
  it('returns empty array when no segments exist', () => {
    const blocks = groupSegmentsIntoParagraphs([], {});
    expect(blocks).toEqual([]);
  });

  it('groups multiple segments ending with is_paragraph_end === true into one block', () => {
    const s0 = createMockSegment(0, false, 'The door creaked open.', 0, false);
    const s1 = createMockSegment(1, true, '"Who is there?" asked John.', 0, true);

    const segmentsByOrder = { 0: s0, 1: s1 };
    const blocks = groupSegmentsIntoParagraphs([0, 1], segmentsByOrder);

    expect(blocks).toHaveLength(1);
    expect(blocks[0].segments).toHaveLength(2);
    expect(blocks[0].segments[0].text).toBe('The door creaked open.');
    expect(blocks[0].segments[1].text).toBe('"Who is there?" asked John.');
  });

  it('splits into multiple blocks across is_paragraph_end boundaries', () => {
    const s0 = createMockSegment(0, true, 'First paragraph.', 0, false);
    const s1 = createMockSegment(1, false, 'Second paragraph part 1.', 1, false);
    const s2 = createMockSegment(2, true, 'Second paragraph part 2.', 1, false);
    const s3 = createMockSegment(3, true, 'Third paragraph.', 2, false);

    const segmentsByOrder = { 0: s0, 1: s1, 2: s2, 3: s3 };
    const blocks = groupSegmentsIntoParagraphs([0, 1, 2, 3], segmentsByOrder);

    expect(blocks).toHaveLength(3);
    expect(blocks[0].segments).toHaveLength(1);
    expect(blocks[1].segments).toHaveLength(2);
    expect(blocks[2].segments).toHaveLength(1);
  });

  it('splits if paragraph_index changes even if is_paragraph_end was omitted', () => {
    const s0 = createMockSegment(0, false, 'Paragraph 0.', 0, false);
    const s1 = createMockSegment(1, false, 'Paragraph 1 begins.', 1, false);

    const segmentsByOrder = { 0: s0, 1: s1 };
    const blocks = groupSegmentsIntoParagraphs([0, 1], segmentsByOrder);

    expect(blocks).toHaveLength(2);
    expect(blocks[0].segments[0].text).toBe('Paragraph 0.');
    expect(blocks[1].segments[0].text).toBe('Paragraph 1 begins.');
  });
});

