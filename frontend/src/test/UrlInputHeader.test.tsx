import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { UrlInputHeader } from '../components/UrlInputHeader';
import { apiClient } from '../services/apiClient';
import { sseManager } from '../services/sseManager';
import { useAppStore } from '../store/useAppStore';
import { ChapterSnapshotResponse } from '../types/api';
import { gaplessAudioEngine } from '../services/GaplessAudioEngine';

const snapshot: ChapterSnapshotResponse = {
  chapter_uuid: 'chapter-1', work_id: '123', work_title: 'Work',
  chapter_title: 'Chapter', chapter_number: 1, total_chapters: 1, author: 'Author',
  source_url: 'https://archiveofourown.org/works/123', status: 'STREAMING',
  error_message: null, run_generation: 1, state_revision: 2,
  review_phase:'SYNTHESIZING',narration_authorized:true,review_revision:1,
  approved_run_generation:1,approved_review_revision:1,approved_at:'fixture',analysis_completed_at:'fixture',
  total_chunks: 1, scripted_chunks: 1, total_segments: 2, ready_segments: 1,
  master_mp3_ready: false, master_mp3_url: null, characters: [],
  segments: [
    { segment_uuid: 'ready', segment_order: 0, character_uuid: 'narrator',
      text: 'Ready', status: 'READY', audio_url: '/ready.wav', duration_ms: 1000,
      is_paragraph_end: true, voice_version: 1 },
    { segment_uuid: 'failed', segment_order: 1, character_uuid: 'narrator',
      text: 'Failed', status: 'FAILED', audio_url: null, duration_ms: null,
      is_paragraph_end: true, voice_version: 1 },
  ],
};

describe('chapter retry control', () => {
  beforeEach(() => {
    localStorage.clear();
    useAppStore.getState().resetWork();
    sseManager.reconcileSnapshot(snapshot);
    vi.spyOn(sseManager, 'connect').mockResolvedValue(undefined);
  });

  afterEach(() => vi.restoreAllMocks());

  it('retries failed segments in a streaming chapter without clearing ready audio or playback', async () => {
    let resolve!: (value: ChapterSnapshotResponse) => void;
    const retry = vi.spyOn(apiClient, 'retryFailed').mockImplementation(() =>
      new Promise((done) => { resolve = done; })
    );
    useAppStore.getState().setPlaybackStatus('PAUSED');
    useAppStore.getState().setActiveSegmentOrder(1);
    render(<UrlInputHeader />);
    fireEvent.click(screen.getByRole('button', { name: 'Retry Failed' }));
    expect(screen.getByRole('button', { name: 'Retrying...' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Load & Analyze' })).toBeDisabled();
    expect(retry).toHaveBeenCalledTimes(1);
    expect(retry).toHaveBeenCalledWith('chapter-1');
    await act(async () => resolve({ ...snapshot, state_revision: 3,
      segments: snapshot.segments.map((segment) => segment.status === 'FAILED'
        ? { ...segment, status: 'QUEUED' } : segment),
    }));
    expect(sseManager.connect).toHaveBeenCalledWith('chapter-1', { saved: true });
    expect(useAppStore.getState().segmentsByOrder[0].audio_url).toBe('/ready.wav');
    expect(useAppStore.getState().segmentsByOrder[1].audio_status).toBe('PENDING_TTS');
    expect(useAppStore.getState().playbackStatus).toBe('PAUSED');
    expect(useAppStore.getState().activeSegmentOrder).toBe(1);
    expect(screen.queryByRole('button', { name: 'Retry Failed' })).not.toBeInTheDocument();
  });

  it('shows request errors and allows another attempt', async () => {
    vi.spyOn(apiClient, 'retryFailed').mockRejectedValue(new Error('Retry unavailable'));
    render(<UrlInputHeader />);
    fireEvent.click(screen.getByRole('button', { name: 'Retry Failed' }));
    expect(await screen.findByText('Retry unavailable')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry Failed' })).toBeEnabled();
    expect(useAppStore.getState().segmentsByOrder[0].audio_url).toBe('/ready.wav');
  });

  it('supports failed chapters even without failed segments', () => {
    sseManager.reconcileSnapshot({ ...snapshot, status: 'FAILED', segments: [] });
    render(<UrlInputHeader />);
    expect(screen.getByRole('button', { name: 'Retry Failed' })).toBeEnabled();
  });

  it('hides retry when neither the chapter nor its segments have failed', () => {
    sseManager.reconcileSnapshot({ ...snapshot, segments: [snapshot.segments[0]] });
    render(<UrlInputHeader />);
    expect(screen.queryByRole('button', { name: 'Retry Failed' })).not.toBeInTheDocument();
  });

  it('does not apply an old retry response after chapter selection changes', async () => {
    let resolve!: (value: ChapterSnapshotResponse) => void;
    vi.spyOn(apiClient, 'retryFailed').mockImplementation(() =>
      new Promise((done) => { resolve = done; })
    );
    render(<UrlInputHeader />);
    fireEvent.click(screen.getByRole('button', { name: 'Retry Failed' }));
    act(() => sseManager.reconcileSnapshot({ ...snapshot, chapter_uuid: 'chapter-2' }));
    await act(async () => resolve(snapshot));
    await waitFor(() => expect(screen.queryByText('Retrying...')).not.toBeInTheDocument());
    expect(useAppStore.getState().metadata?.chapter_id).toBe('chapter-2');
    expect(sseManager.connect).not.toHaveBeenCalled();
  });
});

describe('chapter loading feedback', () => {
  beforeEach(() => {
    localStorage.clear();
    useAppStore.getState().resetWork();
    sseManager.reconcileSnapshot(snapshot);
    vi.spyOn(sseManager, 'connect').mockResolvedValue(undefined);
  });

  afterEach(() => vi.restoreAllMocks());

  it('starts ingestion and shows progress even when audio unlock never settles', async () => {
    vi.spyOn(gaplessAudioEngine, 'unlockAudioContext').mockImplementation(() => new Promise(() => {}));
    let reject!: (reason: Error) => void;
    const ingest = vi.spyOn(apiClient, 'ingestChapter').mockImplementation(() =>
      new Promise((_, fail) => { reject = fail; })
    );
    render(<UrlInputHeader />);
    fireEvent.click(screen.getByRole('button', { name: 'Load & Analyze' }));
    expect(ingest).toHaveBeenCalledWith(snapshot.source_url, 'NONE');
    expect(screen.getByRole('dialog', { name: 'Loading chapter' })).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Fetching chapter text');
    expect(useAppStore.getState().metadata).toBeNull();
    expect(useAppStore.getState().segmentOrders).toEqual([]);
    await act(async () => reject(new Error('AO3 unavailable')));
    expect(screen.getByRole('alert')).toHaveTextContent('AO3 unavailable');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Load & Analyze' })).toBeEnabled();
    expect(useAppStore.getState().pipeline.stage).toBe('IDLE');
  });

  it('surfaces reset failures and releases the load button', async () => {
    vi.spyOn(gaplessAudioEngine, 'reset').mockImplementation(() => { throw new Error('Audio reset failed'); });
    const ingest = vi.spyOn(apiClient, 'ingestChapter');
    render(<UrlInputHeader />);
    fireEvent.click(screen.getByRole('button', { name: 'Load & Analyze' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Audio reset failed');
    expect(screen.getByRole('button', { name: 'Load & Analyze' })).toBeEnabled();
    expect(ingest).not.toHaveBeenCalled();
  });
});
