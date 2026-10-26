import { describe, it, expect, beforeEach } from 'vitest';
import { useAppStore } from '../store/useAppStore';
import { Character, ScriptSegment } from '../types/domain';

describe('useAppStore', () => {
  beforeEach(() => {
    useAppStore.getState().resetWork();
  });

  it('updates metadata and preserves existing values', () => {
    const store = useAppStore.getState();
    store.setMetadata({
      work_id: '123',
      chapter_id: 'chap_1',
      source_url: 'https://ao3.org',
      work_title: 'My Story',
      chapter_title: 'Chapter 1',
      chapter_number: 1,
      total_chapters: 1,
      author: 'Author',
      status: 'IDLE',
      total_chunks: 2,
      scripted_chunks: 0,
      total_segments: 10,
      ready_segments: 0,
      master_mp3_progress: 0,
      master_mp3_url: null,
      error_message: null,
    });

    useAppStore.getState().setMetadata({ ready_segments: 5, status: 'SYNTHESIZING_AUDIO' });

    const current = useAppStore.getState().metadata;
    expect(current?.ready_segments).toBe(5);
    expect(current?.work_title).toBe('My Story');
    expect(current?.status).toBe('SYNTHESIZING_AUDIO');
  });

  it('upserts characters and places Narrator first', () => {
    const chars: Character[] = [
      {
        character_uuid: 'char_bob',
        canonical_name: 'Bob',
        aliases: [],
        is_narrator: false,
        voice_instruct: 'Baritone',
        voice_status: 'READY',
        voice_version: 1,
        preview_url: '/audio/bob.mp3',
        segment_count: 5,
      },
      {
        character_uuid: 'char_narrator',
        canonical_name: 'Narrator',
        aliases: [],
        is_narrator: true,
        voice_instruct: 'Neutral',
        voice_status: 'READY',
        voice_version: 1,
        preview_url: '/audio/narrator.mp3',
        segment_count: 10,
      },
      {
        character_uuid: 'char_alice',
        canonical_name: 'Alice',
        aliases: [],
        is_narrator: false,
        voice_instruct: 'Soprano',
        voice_status: 'READY',
        voice_version: 1,
        preview_url: '/audio/alice.mp3',
        segment_count: 4,
      },
    ];

    useAppStore.getState().upsertCharacters(chars);

    const ids = useAppStore.getState().characterIds;
    expect(ids[0]).toBe('char_narrator');
  });

  it('appends chunk segments and preserves strictly ascending segmentOrders', () => {
    const seg2: ScriptSegment = {
      segment_uuid: 's2',
      segment_order: 2,
      chunk_index: 0,
      paragraph_index: 0,
      is_paragraph_end: true,
      speaker_uuid: 'char_narrator',
      speaker_name: 'Narrator',
      is_dialogue: false,
      text: 'Two',
      audio_status: 'PENDING_TTS',
      audio_url: null,
      duration_ms: null,
      voice_version: 1,
    };

    const seg0: ScriptSegment = { ...seg2, segment_uuid: 's0', segment_order: 0, text: 'Zero' };
    const seg1: ScriptSegment = { ...seg2, segment_uuid: 's1', segment_order: 1, text: 'One' };

    // Append out of order
    useAppStore.getState().appendChunkSegments([seg2, seg0]);
    expect(useAppStore.getState().segmentOrders).toEqual([0, 2]);

    useAppStore.getState().appendChunkSegments([seg1]);
    expect(useAppStore.getState().segmentOrders).toEqual([0, 1, 2]);
  });

  it('marks character segments as STALE_VOICE', () => {
    const seg0: ScriptSegment = {
      segment_uuid: 's0',
      segment_order: 0,
      chunk_index: 0,
      paragraph_index: 0,
      is_paragraph_end: false,
      speaker_uuid: 'char_bob',
      speaker_name: 'Bob',
      is_dialogue: true,
      text: 'Hello',
      audio_status: 'READY',
      audio_url: '/audio/s0.mp3',
      duration_ms: 1200,
      voice_version: 1,
    };
    const seg1: ScriptSegment = {
      ...seg0,
      segment_uuid: 's1',
      segment_order: 1,
      speaker_uuid: 'char_narrator',
      is_dialogue: false,
      text: 'he said.',
    };

    useAppStore.getState().appendChunkSegments([seg0, seg1]);
    useAppStore.getState().markCharacterSegmentsStale('char_bob');

    const s0 = useAppStore.getState().segmentsByOrder[0];
    const s1 = useAppStore.getState().segmentsByOrder[1];

    expect(s0.audio_status).toBe('STALE_VOICE');
    expect(s1.audio_status).toBe('READY');
  });

  it('manages pipeline state and drawer expansion', () => {
    const store = useAppStore.getState();
    expect(store.pipeline.stage).toBe('IDLE');
    expect(store.pipeline.isDrawerExpanded).toBe(false);

    store.setPipelineProgress({
      stage: 'ANALYZING_SCRIPT',
      currentChunk: 1,
      totalChunks: 3,
      totalSegments: 12,
      readySegments: 4,
    });

    const updated = useAppStore.getState().pipeline;
    expect(updated.stage).toBe('ANALYZING_SCRIPT');
    expect(updated.currentChunk).toBe(1);
    expect(updated.totalChunks).toBe(3);
    expect(updated.totalSegments).toBe(12);
    expect(updated.readySegments).toBe(4);

    // Toggle drawer
    useAppStore.getState().togglePipelineDrawer();
    expect(useAppStore.getState().pipeline.isDrawerExpanded).toBe(true);

    useAppStore.getState().togglePipelineDrawer(false);
    expect(useAppStore.getState().pipeline.isDrawerExpanded).toBe(false);

    // resetWork resets pipeline
    useAppStore.getState().resetWork();
    expect(useAppStore.getState().pipeline.stage).toBe('IDLE');
    expect(useAppStore.getState().pipeline.totalChunks).toBe(0);
  });

  it('replaceChapterSnapshot replaces characters and segments atomically and removes absent ones', () => {
    const store = useAppStore.getState();
    // Pre-populate old state
    store.upsertCharacters([
      {
        character_uuid: 'char_old',
        canonical_name: 'Old Character',
        aliases: [],
        is_narrator: false,
        voice_instruct: 'Old prompt',
        voice_status: 'READY',
        voice_version: 1,
        preview_url: null,
        segment_count: 1,
      },
    ]);
    store.appendChunkSegments([
      {
        segment_uuid: 'seg_old',
        segment_order: 10,
        chunk_index: 0,
        paragraph_index: 0,
        is_paragraph_end: true,
        speaker_uuid: 'char_old',
        speaker_name: 'Old Character',
        is_dialogue: true,
        text: 'Old segment',
        audio_status: 'READY',
        audio_url: '/audio/old.mp3',
        duration_ms: 1000,
        voice_version: 1,
      },
    ]);
    store.setActiveSegmentOrder(10);
    store.setPlaybackSpeed(1.5);
    store.setAutoplayEnabled(false);

    // Apply new snapshot with different characters and segments
    store.replaceChapterSnapshot({
      metadata: {
        work_id: 'work_999',
        chapter_id: 'chap_new',
        source_url: 'https://ao3.org/works/999',
        work_title: 'New Story',
        chapter_title: 'Chapter New',
        chapter_number: 2,
        total_chapters: 5,
        author: 'New Author',
        status: 'STREAMING',
        total_chunks: 2,
        scripted_chunks: 1,
        total_segments: 2,
        ready_segments: 1,
        master_mp3_progress: 0,
        master_mp3_url: null,
        error_message: null,
        run_generation: 1,
        state_revision: 5,
      },
      characters: [
        {
          character_uuid: 'char_narrator',
          canonical_name: 'Narrator',
          aliases: [],
          is_narrator: true,
          voice_instruct: 'Clear narrator',
          voice_status: 'READY',
          voice_version: 1,
          preview_url: '/audio/narrator.mp3',
          segment_count: 2,
        },
      ],
      segments: [
        {
          segment_uuid: 'seg_0',
          segment_order: 0,
          chunk_index: 0,
          paragraph_index: 0,
          is_paragraph_end: false,
          speaker_uuid: 'char_narrator',
          speaker_name: 'Narrator',
          is_dialogue: false,
          text: 'First line',
          audio_status: 'READY',
          audio_url: '/audio/s0.mp3',
          duration_ms: 1500,
          voice_version: 1,
        },
        {
          segment_uuid: 'seg_1',
          segment_order: 1,
          chunk_index: 0,
          paragraph_index: 0,
          is_paragraph_end: true,
          speaker_uuid: 'char_narrator',
          speaker_name: 'Narrator',
          is_dialogue: false,
          text: 'Second line',
          audio_status: 'PENDING_TTS',
          audio_url: null,
          duration_ms: null,
          voice_version: 1,
        },
      ],
    });

    const updated = useAppStore.getState();
    // Absent char_old removed
    expect(updated.characters['char_old']).toBeUndefined();
    expect(updated.characterIds).toEqual(['char_narrator']);
    // Absent seg_old (order 10) removed
    expect(updated.segmentsByOrder[10]).toBeUndefined();
    expect(updated.segmentOrders).toEqual([0, 1]);
    // Active order was 10, now clamped to max available order 1
    expect(updated.activeSegmentOrder).toBe(1);
    // User preferences preserved
    expect(updated.playbackSpeed).toBe(1.5);
    expect(updated.autoplayEnabled).toBe(false);
    expect(updated.metadata?.work_title).toBe('New Story');
  });

  it('resetChapterState clears chapter data but preserves user preferences', () => {
    const store = useAppStore.getState();
    store.setPlaybackSpeed(2.0);
    store.setAutoplayEnabled(false);
    store.setAutoScrollEnabled(false);

    store.setMetadata({
      work_id: '123',
      chapter_id: 'chap_1',
      source_url: 'https://ao3.org',
      work_title: 'Story',
      chapter_title: 'Ch 1',
      chapter_number: 1,
      total_chapters: 1,
      author: 'A',
      status: 'COMPLETED',
      total_chunks: 1,
      scripted_chunks: 1,
      total_segments: 1,
      ready_segments: 1,
      master_mp3_progress: 100,
      master_mp3_url: '/master.mp3',
      error_message: null,
    });

    store.resetChapterState();

    const current = useAppStore.getState();
    expect(current.metadata).toBeNull();
    expect(current.characterIds).toEqual([]);
    expect(current.segmentOrders).toEqual([]);
    expect(current.playbackSpeed).toBe(2.0);
    expect(current.autoplayEnabled).toBe(false);
    expect(current.autoScrollEnabled).toBe(false);
  });

  it('setPipelineFailure sets FAILED status and pauses active playback', () => {
    const store = useAppStore.getState();
    store.setMetadata({
      work_id: '123',
      chapter_id: 'chap_1',
      source_url: 'https://ao3.org',
      work_title: 'Story',
      chapter_title: 'Ch 1',
      chapter_number: 1,
      total_chapters: 1,
      author: 'A',
      status: 'STREAMING',
      total_chunks: 2,
      scripted_chunks: 1,
      total_segments: 10,
      ready_segments: 2,
      master_mp3_progress: 0,
      master_mp3_url: null,
      error_message: null,
    });
    store.setPlaybackStatus('WAITING_FOR_TTS');

    store.setPipelineFailure('Qwen3-TTS provider offline', 'TTS_PROVIDER_ERROR');

    const updated = useAppStore.getState();
    expect(updated.metadata?.status).toBe('FAILED');
    expect(updated.metadata?.error_message).toBe('Qwen3-TTS provider offline');
    expect(updated.pipeline.stage).toBe('FAILED');
    expect(updated.playbackStatus).toBe('PAUSED');
  });
});

