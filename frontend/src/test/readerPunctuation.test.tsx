import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render } from '@testing-library/react';
import { ScriptReader } from '../components/ScriptReader';
import { useAppStore } from '../store/useAppStore';
import { ScriptSegment } from '../types/domain';
import { gaplessAudioEngine } from '../services/GaplessAudioEngine';

describe('Canonical reader punctuation', () => {
  beforeEach(() => {
    useAppStore.getState().resetChapterState();
    useAppStore.getState().setMetadata({chapter_id:'approved',review_phase:'SYNTHESIZING',narration_authorized:true});
    useAppStore.getState().setAutoScrollEnabled(false);
    vi.restoreAllMocks();
  });

  function show(parts: { text: string; dialogue?: boolean; paragraph?: number; chunk?: number; end?: boolean }[]) {
    const segments: ScriptSegment[] = parts.map((part, index) => ({
      segment_uuid: `segment-${index}`, segment_order: index,
      chunk_index: part.chunk ?? 0, paragraph_index: part.paragraph ?? 0,
      is_paragraph_end: part.end ?? (index === parts.length - 1 || parts[index + 1].paragraph !== part.paragraph || parts[index + 1].chunk !== part.chunk),
      speaker_uuid: part.dialogue ? 'hero' : 'narrator',
      speaker_name: part.dialogue ? 'Hero' : 'Narrator', is_dialogue: !!part.dialogue,
      text: part.text, audio_status: 'READY', audio_url: null, duration_ms: null, voice_version: 1,
    }));
    useAppStore.getState().appendChunkSegments(segments);
    return render(<ScriptReader />);
  }

  function prose(container: HTMLElement) {
    return Array.from(container.querySelectorAll('[data-reader-text]')).map(node => node.textContent).join('');
  }

  it('preserves interrupted, nested dialogue and unquoted thoughts without prose padding', () => {
    const { container } = show([
      { text: '"Wait,', dialogue: true },
      { text: '" she said, ' },
      { text: '"did he say \'tomorrow\'?"', dialogue: true },
      { text: ' *I don\'t know...*', dialogue: true },
    ]);
    expect(prose(container)).toBe('"Wait," she said, "did he say \'tomorrow\'?" *I don\'t know...*');
    for (const node of container.querySelectorAll('[data-reader-text], [data-segment-order]')) {
      expect(node.className).not.toMatch(/\bpx-/);
    }
    expect(container.querySelectorAll('[data-segment-order]')).toHaveLength(4);
  });

  it('keeps one quote pair across continued dialogue chunks and keeps seeking intact', () => {
    const seek = vi.spyOn(gaplessAudioEngine, 'seekToSegment').mockImplementation(() => {});
    const { container } = show([
      { text: '"The first sentence.', dialogue: true, paragraph: 0, end: false },
      { text: ' The second sentence."', dialogue: true, paragraph: 0, chunk: 1 },
    ]);
    expect(prose(container)).toBe('"The first sentence. The second sentence."');
    expect(container.querySelector('[data-segment-order="0"]')?.parentElement)
      .toBe(container.querySelector('[data-segment-order="1"]')?.parentElement);
    fireEvent.click(container.querySelector('[data-segment-order="1"]')!);
    expect(seek).toHaveBeenCalledWith(1);
  });

  it('renders legacy unquoted records without manufacturing quotes or merging adjacent words', () => {
    const { container } = show([{ text: 'Wait', dialogue: true }, { text: 'she said.' }]);
    expect(prose(container)).toBe('Wait she said.');
    expect(useAppStore.getState().segmentsByOrder[0].text).toBe('Wait');
  });

  it.each([
    [{ text: 'He stopped.' }, { text: 'Then left.' }, 'He stopped. Then left.'],
    [{ text: 'She said,' }, { text: 'not yet.', dialogue: true }, 'She said, not yet.'],
    [{ text: 'A question?' }, { text: 'Another thought.' }, 'A question? Another thought.'],
  ] as const)('keeps punctuation-to-word legacy boundaries readable', (first, second, expected) => {
    const { container } = show([first, second]);
    expect(prose(container)).toBe(expected);
    expect(useAppStore.getState().segmentsByOrder[0].text).toBe(first.text);
  });

  it('never inserts a separator before canonical closing punctuation', () => {
    const { container } = show([{ text: '"Wait', dialogue: true }, { text: '," she said.' }]);
    expect(prose(container)).toBe('"Wait," she said.');
  });
});
