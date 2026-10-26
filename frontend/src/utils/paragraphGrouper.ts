import { ParagraphBlock, ScriptSegment } from '../types/domain';

export function groupSegmentsIntoParagraphs(
  segmentOrders: number[],
  segmentsByOrder: Record<number, ScriptSegment>
): ParagraphBlock[] {
  const blocks: ParagraphBlock[] = [];
  let currentBlock: ParagraphBlock | null = null;
  let currentParaIndex = -1;

  for (const order of segmentOrders) {
    const seg = segmentsByOrder[order];
    if (!seg) continue;

    const segParaIndex = seg.paragraph_index ?? 0;

    // Start a new block if we don't have one, or if paragraph_index changed
    if (!currentBlock || (currentParaIndex !== -1 && segParaIndex !== currentParaIndex)) {
      currentBlock = {
        paragraph_index: segParaIndex,
        segments: [seg],
      };
      blocks.push(currentBlock);
      currentParaIndex = segParaIndex;
    } else {
      currentBlock.segments.push(seg);
    }

    // If this segment is marked as paragraph end, the next segment will begin a new paragraph
    if (seg.is_paragraph_end) {
      currentBlock = null;
      currentParaIndex = -1;
    }
  }

  return blocks;
}

