import { describe, it, expect, beforeEach } from 'vitest';
import { GaplessAudioEngine } from '../services/GaplessAudioEngine';
import { useAppStore } from '../store/useAppStore';
import { ScriptSegment } from '../types/domain';

describe('GaplessAudioEngine', () => {
  let engine: GaplessAudioEngine;

  beforeEach(() => {
    useAppStore.getState().resetWork();
    useAppStore.getState().setMetadata({chapter_id:'approved',review_phase:'SYNTHESIZING',narration_authorized:true,
      run_generation:1,review_revision:1,approved_run_generation:1,approved_review_revision:1,approved_at:'fixture'});
    engine = new GaplessAudioEngine();
  });

  it('initializes in unlocked state after calling unlockAudioContext', async () => {
    await expect(engine.unlockAudioContext()).resolves.toBeUndefined();
  });

  it('updates playback speed and applies to store', () => {
    engine.setSpeed(1.5);
    expect(useAppStore.getState().playbackSpeed).toBe(1.5);
  });

  it('sets WAITING_FOR_TTS when seeking to unsynthesized segment', () => {
    const seg: ScriptSegment = {
      segment_uuid: 'seg_1',
      segment_order: 1,
      chunk_index: 0,
      paragraph_index: 0,
      is_paragraph_end: false,
      speaker_uuid: 'char_narrator',
      speaker_name: 'Narrator',
      is_dialogue: false,
      text: 'Pending segment text',
      audio_status: 'PENDING_TTS',
      audio_url: null,
      duration_ms: null,
      voice_version: 1,
    };
    useAppStore.getState().appendChunkSegments([seg]);

    engine.seekToSegment(1);

    expect(useAppStore.getState().activeSegmentOrder).toBe(1);
    expect(useAppStore.getState().playbackStatus).toBe('WAITING_FOR_TTS');
  });

  it('swaps and plays when onSegmentReady is invoked for currently waiting segment', async () => {
    const seg: ScriptSegment = {
      segment_uuid: 'seg_0',
      segment_order: 0,
      chunk_index: 0,
      paragraph_index: 0,
      is_paragraph_end: false,
      speaker_uuid: 'char_narrator',
      speaker_name: 'Narrator',
      is_dialogue: false,
      text: 'Hello world',
      audio_status: 'PENDING_TTS',
      audio_url: null,
      duration_ms: null,
      voice_version: 1,
    };
    useAppStore.getState().appendChunkSegments([seg]);
    useAppStore.getState().setActiveSegmentOrder(0);
    useAppStore.getState().setPlaybackStatus('WAITING_FOR_TTS');

    // Make segment ready in store
    useAppStore.getState().updateSegmentAudio(0, {
      audio_status: 'READY',
      audio_url: '/audio/seg_0.mp3',
      duration_ms: 1800,
    });

    // Notify engine
    await engine.onSegmentReady(0);

    expect(useAppStore.getState().playbackStatus).toBe('PLAYING');
  });

  it('pauses and updates store state', () => {
    useAppStore.getState().setPlaybackStatus('PLAYING');
    engine.pause();
    expect(useAppStore.getState().playbackStatus).toBe('PAUSED');
  });

  it('waiting pause prevents later ready auto-start', async () => {
    const seg: ScriptSegment = {
      segment_uuid: 'seg_5',
      segment_order: 5,
      chunk_index: 0,
      paragraph_index: 0,
      is_paragraph_end: false,
      speaker_uuid: 'char_narrator',
      speaker_name: 'Narrator',
      is_dialogue: false,
      text: 'Waiting segment',
      audio_status: 'PENDING_TTS',
      audio_url: null,
      duration_ms: null,
      voice_version: 1,
    };
    useAppStore.getState().appendChunkSegments([seg]);
    useAppStore.getState().setActiveSegmentOrder(5);

    // User clicked Play on unready segment -> entered waiting
    await engine.play();
    expect(useAppStore.getState().playbackStatus).toBe('WAITING_FOR_TTS');

    // User paused (via mouse or Space key)
    engine.pause();
    expect(useAppStore.getState().playbackStatus).toBe('PAUSED');
    expect(engine.getPlayIntent()).toBe('NONE');

    // Segment now finishes synthesis
    useAppStore.getState().updateSegmentAudio(5, {
      audio_status: 'READY',
      audio_url: '/audio/seg_5.mp3',
      duration_ms: 2000,
    });
    await engine.onSegmentReady(5);

    // Playback MUST NOT auto-start!
    expect(useAppStore.getState().playbackStatus).toBe('PAUSED');
  });

  it('explicit play still works when autoplay is disabled', async () => {
    useAppStore.getState().setAutoplayEnabled(false);
    const seg: ScriptSegment = {
      segment_uuid: 'seg_ready',
      segment_order: 0,
      chunk_index: 0,
      paragraph_index: 0,
      is_paragraph_end: false,
      speaker_uuid: 'char_narrator',
      speaker_name: 'Narrator',
      is_dialogue: false,
      text: 'Ready segment',
      audio_status: 'READY',
      audio_url: '/audio/seg_ready.mp3',
      duration_ms: 2000,
      voice_version: 1,
    };
    useAppStore.getState().appendChunkSegments([seg]);
    useAppStore.getState().setActiveSegmentOrder(0);

    // Explicit user play call
    await engine.play();
    expect(useAppStore.getState().playbackStatus).toBe('PLAYING');
  });

  it('real engine media rejection never leaves PLAYING and sets PAUSED', async () => {
    const seg: ScriptSegment = {
      segment_uuid: 'seg_corrupt',
      segment_order: 0,
      chunk_index: 0,
      paragraph_index: 0,
      is_paragraph_end: false,
      speaker_uuid: 'char_narrator',
      speaker_name: 'Narrator',
      is_dialogue: false,
      text: 'Corrupt segment',
      audio_status: 'READY',
      audio_url: '/audio/corrupt.mp3',
      duration_ms: 2000,
      voice_version: 1,
    };
    useAppStore.getState().appendChunkSegments([seg]);
    useAppStore.getState().setActiveSegmentOrder(0);

    // Mock play to reject (e.g. unsupported format, decoding error, network failure)
    const originalPlay = window.HTMLMediaElement.prototype.play;
    window.HTMLMediaElement.prototype.play = () => Promise.reject(new Error('NotAllowedError / MediaDecodeError'));

    try {
      await engine.play();
      expect(useAppStore.getState().playbackStatus).toBe('PAUSED');
      expect(engine.getPlayIntent()).toBe('NONE');
    } finally {
      window.HTMLMediaElement.prototype.play = originalPlay;
    }
  });

  it('chapter reset stops audio, clears intent, and cancels timers', () => {
    useAppStore.getState().setPlaybackStatus('WAITING_FOR_TTS');
    engine.seekToSegment(2);
    expect(engine.getPlayIntent()).toBe('USER_PLAY');

    engine.reset();
    expect(engine.getPlayIntent()).toBe('NONE');
  });
});
