import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { sseManager } from '../services/sseManager';
import { apiClient } from '../services/apiClient';
import { useAppStore } from '../store/useAppStore';
import { ChapterSnapshotResponse } from '../types/api';
import { gaplessAudioEngine } from '../services/GaplessAudioEngine';

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  listeners: Record<string, ((e: MessageEvent) => void)[]> = {};
  onerror: (() => void) | null = null;
  onopen: (() => void) | null = null;
  closed = false;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
    setTimeout(() => {
      if (!this.closed && this.onopen) {
        this.onopen();
      }
    }, 0);
  }

  addEventListener(type: string, listener: (e: MessageEvent) => void) {
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(listener);
  }

  removeEventListener(type: string, listener: (e: MessageEvent) => void) {
    if (this.listeners[type]) {
      this.listeners[type] = this.listeners[type].filter((l) => l !== listener);
    }
  }

  emit(type: string, data: any) {
    const event = new MessageEvent(type, { data: JSON.stringify(data) });
    const list = this.listeners[type] || [];
    for (const l of list) {
      l(event);
    }
  }

  close() {
    this.closed = true;
  }
}

describe('SSEManager and Status Reconciliation', () => {
  const originalEventSource = globalThis.EventSource;

  beforeEach(() => {
    FakeEventSource.instances = [];
    // @ts-ignore
    globalThis.EventSource = FakeEventSource;
    useAppStore.getState().resetChapterState();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    sseManager.disconnect();
    globalThis.EventSource = originalEventSource;
  });

  const sampleSnapshot: ChapterSnapshotResponse = {
    chapter_uuid: 'chap_100',
    work_id: '42423831',
    work_title: 'Title',
    chapter_title: 'Ch 1',
    chapter_number: 1,
    total_chapters: 2,
    author: 'Author',
    source_url: 'https://ao3.org/works/42423831',
    status: 'STREAMING',
    error_message: null,
    run_generation: 1,
    state_revision: 10,
    total_chunks: 2,
    scripted_chunks: 1,
    master_mp3_ready: false,
    master_mp3_url: null,
    total_segments: 2,
    ready_segments: 1,
    characters: [
      {
        character_uuid: 'char_narrator',
        canonical_name: 'Narrator',
        is_narrator: true,
        voice_instruct: 'Narrator',
        voice_version: 1,
        preview_url: '/audio/narrator.mp3',
        voice_status: 'READY',
      },
    ],
    segments: [
      {
        segment_uuid: 'seg_0',
        segment_order: 0,
        character_uuid: 'char_narrator',
        text: 'Hello',
        status: 'READY',
        audio_url: '/audio/seg_0.mp3',
        duration_ms: 1200,
        is_paragraph_end: false,
        voice_version: 1,
      },
      {
        segment_uuid: 'seg_1',
        segment_order: 1,
        character_uuid: 'char_narrator',
        text: 'World',
        status: 'QUEUED',
        audio_url: null,
        duration_ms: null,
        is_paragraph_end: true,
        voice_version: 1,
      },
    ],
  };

  it('opens saved chapters without invoking autoplay despite the generation preference', async () => {
    useAppStore.getState().setAutoplayEnabled(true);
    vi.spyOn(apiClient, 'getChapterSnapshot').mockResolvedValue(sampleSnapshot);
    vi.spyOn(gaplessAudioEngine, 'getPlayIntent').mockReturnValue('NONE');
    const ready = vi.spyOn(gaplessAudioEngine, 'onSegmentReady');
    await sseManager.connect('chap_100', { saved: true });
    expect(ready).not.toHaveBeenCalled();
    expect(useAppStore.getState().playbackStatus).toBe('IDLE');
  });

  it('propagates saved snapshot failures without opening an event stream', async () => {
    vi.spyOn(apiClient, 'getChapterSnapshot').mockRejectedValue(new Error('Not found'));
    await expect(sseManager.connect('missing', { saved: true })).rejects.toThrow('Not found');
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it.each(['STREAMING', 'FAILED'] as const)('opens saved %s chapters paused and accepts later script updates', async (status) => {
    const ingest = vi.spyOn(apiClient, 'ingestChapter');
    const ready = vi.spyOn(gaplessAudioEngine, 'onSegmentReady');
    useAppStore.getState().setAutoplayEnabled(true);
    vi.spyOn(apiClient, 'getChapterSnapshot').mockResolvedValue({
      ...sampleSnapshot, status,
      segments: status === 'STREAMING' ? [] : sampleSnapshot.segments,
    });
    await sseManager.connect('chap_100', { saved: true });
    expect(useAppStore.getState().metadata?.status).toBe(status);
    expect(useAppStore.getState().segmentOrders).toHaveLength(status === 'STREAMING' ? 0 : 2);
    expect(useAppStore.getState().playbackStatus).toBe('IDLE');
    expect(ingest).not.toHaveBeenCalled();
    expect(ready).not.toHaveBeenCalled();
    if (status === 'STREAMING') {
      FakeEventSource.instances[0].emit('chunk_scripted', {
        chapter_uuid: 'chap_100', run_generation: 1, state_revision: 11,
        chunk_index: 0, scripted_chunks: 1, total_chunks: 2,
        segments: sampleSnapshot.segments,
      });
      expect(useAppStore.getState().segmentOrders).toHaveLength(2);
      expect(useAppStore.getState().playbackStatus).toBe('IDLE');
      expect(ready).not.toHaveBeenCalled();
    }
  });

  it('reconciles snapshot with correct status mappings and revision tracking', async () => {
    vi.spyOn(apiClient, 'getChapterSnapshot').mockResolvedValue(sampleSnapshot);

    await sseManager.connect('chap_100');

    const store = useAppStore.getState();
    expect(store.metadata?.status).toBe('STREAMING');
    expect(store.metadata?.run_generation).toBe(1);
    expect(store.metadata?.state_revision).toBe(10);
    expect(store.segmentsByOrder[0].audio_status).toBe('READY');
    expect(store.segmentsByOrder[1].audio_status).toBe('PENDING_TTS');
  });

  it('preserves identical punctuation through snapshot and JSON streamed hydration', async () => {
    const text = '"Did he say \'tomorrow\'?" she asked. *I don\'t know...*';
    const segment = { ...sampleSnapshot.segments[0], text };
    vi.spyOn(apiClient, 'getChapterSnapshot').mockResolvedValue({
      ...sampleSnapshot, segments: [segment],
    });
    await sseManager.connect('chap_100', { saved: true });
    expect(useAppStore.getState().segmentsByOrder[0].text).toBe(text);
    FakeEventSource.instances[0].emit('chunk_scripted', {
      chapter_uuid: 'chap_100', run_generation: 1, state_revision: 11,
      chunk_index: 0, scripted_chunks: 1, total_chunks: 2,
      segments: [segment],
    });
    expect(useAppStore.getState().segmentsByOrder[0].text).toBe(text);
    expect(useAppStore.getState().segmentsByOrder[0].text).not.toContain('\\"');
  });

  it('ignores stale SSE events from older generations or revisions <= lastAppliedRevision', async () => {
    vi.spyOn(apiClient, 'getChapterSnapshot').mockResolvedValue(sampleSnapshot);

    await sseManager.connect('chap_100');
    const es = FakeEventSource.instances[0];
    expect(es).toBeDefined();

    // Replayed event with revision 8 (older than snapshot revision 10)
    es.emit('chapter_status', {
      chapter_uuid: 'chap_100',
      run_generation: 1,
      state_revision: 8,
      status: 'FAILED',
      error_message: 'Stale error',
    });

    // Store MUST NOT have applied the stale failure
    expect(useAppStore.getState().metadata?.status).toBe('STREAMING');
    expect(useAppStore.getState().pipeline.stage).not.toBe('FAILED');

    // Newer event with revision 11
    es.emit('chapter_status', {
      chapter_uuid: 'chap_100',
      run_generation: 1,
      state_revision: 11,
      status: 'MASTERING',
      error_message: null,
    });

    expect(useAppStore.getState().metadata?.status).toBe('MASTERING');
    expect(useAppStore.getState().pipeline.stage).toBe('MASTERING');
  });

  it('ignores SSE events belonging to a different chapter', async () => {
    vi.spyOn(apiClient, 'getChapterSnapshot').mockResolvedValue(sampleSnapshot);

    await sseManager.connect('chap_100');
    const es = FakeEventSource.instances[0];

    // Event targeting a different chapter_uuid
    es.emit('chapter_failed', {
      chapter_uuid: 'chap_999_other',
      run_generation: 1,
      state_revision: 20,
      status: 'FAILED',
      error_message: 'Other chapter failed',
    });

    expect(useAppStore.getState().metadata?.status).toBe('STREAMING');
  });

  it('chapter_failed sets pipeline failure and disconnects EventSource', async () => {
    vi.spyOn(apiClient, 'getChapterSnapshot').mockResolvedValue(sampleSnapshot);

    await sseManager.connect('chap_100');
    const es = FakeEventSource.instances[0];

    es.emit('chapter_failed', {
      chapter_uuid: 'chap_100',
      run_generation: 1,
      state_revision: 12,
      status: 'FAILED',
      error_code: 'TTS_PROVIDER_ERROR',
      error_message: 'TTS model unavailable',
    });

    const store = useAppStore.getState();
    expect(store.metadata?.status).toBe('FAILED');
    expect(store.metadata?.error_message).toBe('TTS model unavailable');
    expect(store.pipeline.stage).toBe('FAILED');
    expect(es.closed).toBe(true);
  });

  it('segment_failed maps to ERROR and records error code and message', async () => {
    vi.spyOn(apiClient, 'getChapterSnapshot').mockResolvedValue(sampleSnapshot);

    await sseManager.connect('chap_100');
    const es = FakeEventSource.instances[0];

    es.emit('segment_failed', {
      chapter_uuid: 'chap_100',
      run_generation: 1,
      state_revision: 12,
      segment_uuid: 'seg_1',
      segment_order: 1,
      status: 'FAILED',
      retry_count: 3,
      error_code: 'AUDIO_EMPTY',
      error_message: 'Synthesized audio was empty',
    });

    const seg1 = useAppStore.getState().segmentsByOrder[1];
    expect(seg1.audio_status).toBe('ERROR');
    expect(seg1.error_message).toBe('Synthesized audio was empty');
    expect(seg1.last_error_code).toBe('AUDIO_EMPTY');
    expect(seg1.retry_count).toBe(3);
  });

  it('snapshot_required triggers a full snapshot fetch', async () => {
    const fetchSpy = vi.spyOn(apiClient, 'getChapterSnapshot').mockResolvedValue(sampleSnapshot);

    await sseManager.connect('chap_100');
    expect(fetchSpy).toHaveBeenCalledTimes(1);

    const es = FakeEventSource.instances[0];
    es.emit('snapshot_required', {
      chapter_uuid: 'chap_100',
      reason: 'history_evicted',
    });

    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it('ignores late events after disconnecting the previous run', async () => {
    vi.spyOn(apiClient, 'getChapterSnapshot').mockResolvedValue(sampleSnapshot);

    await sseManager.connect('chap_100');
    const es = FakeEventSource.instances[0];
    sseManager.disconnect();
    useAppStore.getState().resetChapterState();

    es.emit('chapter_failed', {
      chapter_uuid: 'chap_100',
      run_generation: 1,
      state_revision: 99,
      status: 'FAILED',
      error_message: 'Old run failure',
    });

    expect(useAppStore.getState().metadata).toBeNull();
    expect(useAppStore.getState().pipeline.stage).toBe('IDLE');
  });

  it('rejects old voice-ready events when reloading the same chapter', async () => {
    vi.spyOn(apiClient, 'getChapterSnapshot')
      .mockResolvedValueOnce(sampleSnapshot)
      .mockResolvedValueOnce({
        ...sampleSnapshot,
        run_generation: 2,
        state_revision: 20,
        characters: [],
        segments: [],
        total_segments: 0,
        ready_segments: 0,
      });
    await sseManager.connect('chap_100');
    const oldSource = FakeEventSource.instances[0];
    sseManager.disconnect();
    useAppStore.getState().resetChapterState();
    await sseManager.connect('chap_100');
    const newSource = FakeEventSource.instances[1];

    const oldVoice = {
      chapter_uuid: 'chap_100', run_generation: 1, state_revision: 99,
      character_uuid: 'old_voice', canonical_name: 'Old Voice',
      voice_version: 1, preview_url: '/old.mp3', voice_instruct: 'Old prompt',
    };
    oldSource.emit('character_voice_ready', oldVoice);
    newSource.emit('character_voice_ready', oldVoice);
    expect(useAppStore.getState().characterIds).toEqual([]);

    newSource.emit('character_voice_ready', {
      ...oldVoice, run_generation: 2, state_revision: 21,
      character_uuid: 'new_voice', canonical_name: 'New Voice', preview_url: '/new.mp3',
    });
    expect(useAppStore.getState().characterIds).toEqual(['new_voice']);
  });
});

