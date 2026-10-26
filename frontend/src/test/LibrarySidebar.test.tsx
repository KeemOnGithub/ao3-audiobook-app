import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LibrarySidebar } from '../components/LibrarySidebar';
import { UrlInputHeader } from '../components/UrlInputHeader';
import { sseManager } from '../services/sseManager';
import { gaplessAudioEngine } from '../services/GaplessAudioEngine';
import { apiClient } from '../services/apiClient';
import { useAppStore } from '../store/useAppStore';
import type { LibraryResponse } from '../types/api';

vi.mock('../services/apiClient', () => ({ apiClient: { getLibrary: vi.fn(), ingestChapter: vi.fn(), getMasterDownloadUrl: vi.fn(() => '/master.mp3') } }));
vi.mock('../services/sseManager', () => ({ sseManager: { connect: vi.fn(), disconnect: vi.fn() } }));
vi.mock('../services/GaplessAudioEngine', () => ({ gaplessAudioEngine: { reset: vi.fn(), unlockAudioContext: vi.fn(), setAutoplayIntent: vi.fn() } }));

const library: LibraryResponse = {
  works: [{ work_id: 'work-1', title: 'A saved story', author: 'An author', chapters: [{
    chapter_uuid: 'uuid-1', chapter_id: 'source-1', chapter_number: 1,
    chapter_title: 'Arrival', source_url: 'https://archiveofourown.org/works/1/chapters/1',
    status: 'COMPLETED', master_audio_available: false,
  }] }],
};

beforeEach(() => {
  localStorage.clear();
  vi.mocked(apiClient.getLibrary).mockReset().mockResolvedValue(library);
  useAppStore.getState().resetChapterState();
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };
});

afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('LibrarySidebar', () => {
  it('groups chapters, expands the active UUID, and delegates opening', async () => {
    useAppStore.getState().setMetadata({ chapter_id: 'uuid-1' });
    const onSelect = vi.fn().mockResolvedValue(undefined);
    render(<LibrarySidebar onSelect={onSelect} />);
    const chapter = await screen.findByRole('button', { name: /Chapter 1: Arrival/ });
    expect(chapter).toHaveAttribute('aria-current', 'true');
    expect(screen.getByText('An author')).toBeInTheDocument();
    expect(screen.getByText('Master audio unavailable')).toBeInTheDocument();
    fireEvent.click(chapter);
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith(library.works[0].chapters[0]));
    act(() => useAppStore.getState().resetChapterState());
    expect(screen.getByRole('button', { name: /A saved story/ })).toHaveAttribute('aria-expanded', 'true');
  });

  it('shows initial failure, retries, and retains results on refresh failure', async () => {
    vi.mocked(apiClient.getLibrary).mockRejectedValueOnce(new Error('offline'));
    render(<LibrarySidebar onSelect={vi.fn()} />);
    expect(screen.getByRole('status')).toHaveTextContent('Loading library');
    fireEvent.click(await screen.findByRole('button', { name: 'Retry library' }));
    await screen.findByText('A saved story');
    vi.mocked(apiClient.getLibrary).mockRejectedValueOnce(new Error('offline'));
    fireEvent.focus(window);
    await screen.findByRole('alert');
    expect(screen.getByText('A saved story')).toBeInTheDocument();
  });

  it('renders an empty library', async () => {
    vi.mocked(apiClient.getLibrary).mockResolvedValue({ works: [] });
    render(<LibrarySidebar onSelect={vi.fn()} />);
    expect(await screen.findByText(/No saved chapters yet/)).toBeInTheDocument();
  });

  it('traps mobile focus, dismisses on Escape, and restores the trigger', async () => {
    render(<LibrarySidebar onSelect={vi.fn()} />);
    await screen.findByText('A saved story');
    const trigger = screen.getByRole('button', { name: 'Library' });
    fireEvent.click(trigger);
    const close = screen.getByRole('button', { name: 'Close library' });
    expect(close).toHaveFocus();
    fireEvent.keyDown(close, { key: 'Tab', shiftKey: true });
    expect(screen.getByRole('button', { name: /A saved story/ })).toHaveFocus();
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    expect(document.body.style.overflow).toBe('');
  });

  it('deduplicates requests and refreshes on completion and visible intervals', async () => {
    vi.useFakeTimers();
    render(<LibrarySidebar onSelect={vi.fn()} />);
    await act(async () => {});
    act(() => useAppStore.getState().setMetadata({ chapter_id: 'uuid-1', status: 'COMPLETED' }));
    await act(async () => {});
    expect(apiClient.getLibrary).toHaveBeenCalledTimes(2);
    await act(async () => { vi.advanceTimersByTime(30_000); });
    expect(apiClient.getLibrary).toHaveBeenCalledTimes(3);
    cleanup();
    await act(async () => { vi.advanceTimersByTime(60_000); });
    expect(apiClient.getLibrary).toHaveBeenCalledTimes(3);
  });

  it.each([
    ['PENDING', 'Queued for processing', 'bg-orange-400'],
    ['STREAMING', 'Processing', 'bg-orange-400'],
    ['MASTERING', 'Finalizing audio', 'bg-orange-400'],
    ['COMPLETED', 'Audio processing complete', 'bg-emerald-400'],
    ['FAILED', 'Processing failed or interrupted', 'bg-red-400'],
  ] as const)('renders accessible %s status and honest audio warnings', async (status, text, color) => {
    const chapter = { ...library.works[0].chapters[0], status, chapter_title: 'A very long chapter title '.repeat(15) };
    vi.mocked(apiClient.getLibrary).mockResolvedValue({ works: [{ ...library.works[0], chapters: [chapter] }] });
    useAppStore.getState().setMetadata({ chapter_id: 'uuid-1' });
    const onSelect = vi.fn().mockResolvedValue(undefined);
    render(<LibrarySidebar onSelect={onSelect} />);
    const button = await screen.findByRole('button', { name: new RegExp(`Chapter 1:.*${text}`) });
    expect(button).toHaveAttribute('aria-current', 'true');
    expect(button).toHaveClass('bg-indigo-500/15');
    const dot = button.querySelector(`.${color}`)!;
    expect(dot).toHaveAttribute('aria-hidden', 'true');
    expect(dot).toHaveClass('shrink-0', 'h-2', 'w-2');
    expect(button.querySelector('[title]')).toHaveAttribute('title', text);
    expect(screen.queryByText('Master audio unavailable') !== null).toBe(status === 'COMPLETED');
    fireEvent.click(button);
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith(chapter));
  });

  it('invalidates on identity, generation and lifecycle scalars but not progress or reset', async () => {
    render(<LibrarySidebar onSelect={vi.fn()} />);
    await waitFor(() => expect(apiClient.getLibrary).toHaveBeenCalledTimes(1));
    await act(async () => useAppStore.getState().setMetadata({ chapter_id: 'uuid-1', run_generation: 1, status: 'STREAMING' }));
    expect(apiClient.getLibrary).toHaveBeenCalledTimes(2);
    await act(async () => useAppStore.getState().setMetadata({ scripted_chunks: 1, ready_segments: 2 }));
    expect(apiClient.getLibrary).toHaveBeenCalledTimes(2);
    await act(async () => useAppStore.getState().setMetadata({ run_generation: 2 }));
    expect(apiClient.getLibrary).toHaveBeenCalledTimes(3);
    await act(async () => useAppStore.getState().setMetadata({ status: 'MASTERING',review_phase:'MASTERING' }));
    expect(apiClient.getLibrary).toHaveBeenCalledTimes(4);
    await act(async () => useAppStore.getState().setMetadata({ status: 'FAILED',review_phase:'FAILED' }));
    expect(apiClient.getLibrary).toHaveBeenCalledTimes(5);
    await act(async () => useAppStore.getState().resetChapterState());
    expect(apiClient.getLibrary).toHaveBeenCalledTimes(5);
    expect(screen.getByRole('button', { name: /Chapter 1: Arrival/ })).toBeVisible();
  });

  it('keeps regenerated rows visible while refreshed lifecycle status changes the dot', async () => {
    useAppStore.getState().setMetadata({ chapter_id: 'uuid-1', status: 'COMPLETED', run_generation: 1 });
    render(<LibrarySidebar onSelect={vi.fn()} />);
    await screen.findByRole('button', { name: /Chapter 1: Arrival Audio processing complete/ });
    const streaming = { ...library.works[0].chapters[0], status: 'STREAMING' as const };
    vi.mocked(apiClient.getLibrary).mockResolvedValue({ works: [{ ...library.works[0], chapters: [streaming] }] });
    await act(async () => useAppStore.getState().setMetadata({ status: 'STREAMING', run_generation: 2 }));
    const row = screen.getByRole('button', { name: /Chapter 1: Arrival Processing/ });
    expect(row.querySelector('.bg-orange-400')).not.toBeNull();
    expect(row).toHaveAttribute('aria-current', 'true');
    expect(screen.queryByText('Master audio unavailable')).not.toBeInTheDocument();
    expect(screen.getByText('1 saved chapter')).toBeInTheDocument();
  });

  it('queues discovery behind a pre-load GET when successful STREAMING metadata is accepted', async () => {
    let resolveList!: (value: LibraryResponse) => void;
    vi.mocked(apiClient.getLibrary).mockImplementationOnce(() => new Promise((resolve) => { resolveList = resolve; }));
    vi.mocked(apiClient.ingestChapter).mockResolvedValue({
      chapter_uuid: 'uuid-1', chapter_id: 'source-1', work_id: 'work-1', title: 'Arrival',
      work_title: 'A saved story', chapter_title: 'Arrival', chapter_number: 1,
      total_chapters: 1, author: 'An author', status: 'STREAMING', total_chunks: 7,
      run_generation: 1, state_revision: 1,
    });
    vi.mocked(sseManager.connect).mockResolvedValue(undefined);
    vi.mocked(gaplessAudioEngine.unlockAudioContext).mockResolvedValue(undefined);
    render(<><UrlInputHeader /><LibrarySidebar onSelect={vi.fn()} /></>);
    fireEvent.change(screen.getByPlaceholderText(/archiveofourown/), { target: { value: library.works[0].chapters[0].source_url } });
    fireEvent.click(screen.getByRole('button', { name: 'Load & Analyze' }));
    await waitFor(() => expect(useAppStore.getState().metadata?.status).toBe('STREAMING'));
    act(() => useAppStore.getState().setMetadata({ run_generation: 2, status: 'MASTERING' }));
    expect(apiClient.getLibrary).toHaveBeenCalledTimes(1);
    await act(async () => resolveList({ works: [] }));
    await screen.findByRole('button', { name: /Chapter 1: Arrival/ });
    expect(apiClient.getLibrary).toHaveBeenCalledTimes(2);
    expect(screen.getByRole('button', { name: /A saved story/ })).toHaveAttribute('aria-expanded', 'true');
  });

  it('pauses hidden-page timers and ignores queued responses after teardown', async () => {
    vi.useFakeTimers();
    render(<LibrarySidebar onSelect={vi.fn()} />);
    await act(async () => {});
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    fireEvent(document, new Event('visibilitychange'));
    await act(async () => { vi.advanceTimersByTime(90_000); });
    expect(apiClient.getLibrary).toHaveBeenCalledTimes(1);
    let resolve!: (value: LibraryResponse) => void;
    vi.mocked(apiClient.getLibrary).mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    fireEvent(document, new Event('visibilitychange'));
    act(() => useAppStore.getState().setMetadata({ chapter_id: 'new', status: 'STREAMING' }));
    cleanup();
    await act(async () => resolve(library));
    expect(apiClient.getLibrary).toHaveBeenCalledTimes(2);
  });
});
