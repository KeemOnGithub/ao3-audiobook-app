import { beforeEach, describe, expect, it, vi } from 'vitest';
import { beginChapterSelection, openSavedChapter } from '../services/chapterSelection';
import { sseManager } from '../services/sseManager';
import { gaplessAudioEngine } from '../services/GaplessAudioEngine';
import { useAppStore } from '../store/useAppStore';

vi.mock('../services/sseManager', () => ({ sseManager: { connect: vi.fn(), disconnect: vi.fn() } }));
vi.mock('../services/GaplessAudioEngine', () => ({ gaplessAudioEngine: { reset: vi.fn() } }));

describe('saved chapter selection', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    localStorage.clear();
    useAppStore.getState().resetChapterState();
    beginChapterSelection();
  });

  it('resets playback and opens a saved snapshot without changing autoplay', async () => {
    useAppStore.getState().setAutoplayEnabled(true);
    vi.mocked(sseManager.connect).mockImplementation(async () => {
      useAppStore.getState().setMetadata({ chapter_id: 'saved', source_url: 'https://example.com/saved' });
    });
    expect(await openSavedChapter('saved')).toBe(true);
    expect(sseManager.connect).toHaveBeenCalledWith('saved', { saved: true });
    expect(gaplessAudioEngine.reset).toHaveBeenCalled();
    expect(useAppStore.getState().playbackStatus).toBe('IDLE');
    expect(useAppStore.getState().autoplayEnabled).toBe(true);
    expect(localStorage.getItem('ao3_audiobook_last_chapter_uuid')).toBe('saved');
  });

  it('does not persist a stale selection or surface its error', async () => {
    let rejectFirst!: (error: Error) => void;
    vi.mocked(sseManager.connect).mockImplementationOnce(() => new Promise((_, reject) => { rejectFirst = reject; }));
    const first = openSavedChapter('first');
    vi.mocked(sseManager.connect).mockImplementationOnce(async () => {
      useAppStore.getState().setMetadata({ chapter_id: 'second', source_url: 'https://example.com/second' });
    });
    await openSavedChapter('second');
    rejectFirst(new Error('stale error'));
    expect(await first).toBe(false);
    expect(localStorage.getItem('ao3_audiobook_last_chapter_uuid')).toBe('second');
  });

  it('retains the previous successful selection on failure', async () => {
    localStorage.setItem('ao3_audiobook_last_chapter_uuid', 'previous');
    vi.mocked(sseManager.connect).mockRejectedValue(new Error('Not found'));
    await expect(openSavedChapter('missing')).rejects.toThrow('Not found');
    expect(localStorage.getItem('ao3_audiobook_last_chapter_uuid')).toBe('previous');
  });

  it.each(['STREAMING', 'FAILED'] as const)('opens %s saved content for inspection without autoplay', async (status) => {
    useAppStore.getState().setAutoplayEnabled(true);
    vi.mocked(sseManager.connect).mockImplementation(async () => {
      useAppStore.getState().setMetadata({ chapter_id: 'partial', status, source_url: 'https://example.com/partial' });
    });
    expect(await openSavedChapter('partial')).toBe(true);
    expect(sseManager.connect).toHaveBeenCalledTimes(1);
    expect(sseManager.connect).toHaveBeenCalledWith('partial', { saved: true });
    expect(useAppStore.getState().metadata?.status).toBe(status);
    expect(useAppStore.getState().playbackStatus).toBe('IDLE');
    expect(useAppStore.getState().autoplayEnabled).toBe(true);
  });

  it('invalidates saved selection when URL loading starts', async () => {
    let resolve!: () => void;
    vi.mocked(sseManager.connect).mockImplementationOnce(() => new Promise<void>((done) => { resolve = done; }));
    const pending = openSavedChapter('old');
    beginChapterSelection();
    resolve();
    expect(await pending).toBe(false);
    expect(localStorage.getItem('ao3_audiobook_last_chapter_uuid')).toBeNull();
  });
});
