import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LibrarySidebar } from '../components/LibrarySidebar';
import { apiClient } from '../services/apiClient';
import { useAppStore } from '../store/useAppStore';
import type { LibraryResponse } from '../types/api';

vi.mock('../services/apiClient', () => ({
  apiClient: {
    getLibrary: vi.fn(),
    ingestChapter: vi.fn(),
    getMasterDownloadUrl: vi.fn(() => '/master.mp3'),
  },
}));

const mockLibrary: LibraryResponse = {
  works: [
    {
      work_id: 'work-1',
      title: 'The Debt of Time',
      author: 'Shayalonnie',
      chapters: [
        {
          chapter_uuid: 'uuid-1',
          chapter_id: 'source-1',
          chapter_number: 14,
          chapter_title: 'Whispering Library',
          source_url: 'https://archiveofourown.org/works/1/chapters/14',
          status: 'COMPLETED',
          master_audio_available: true,
        },
      ],
    },
    {
      work_id: 'work-2',
      title: 'All the Young Dudes',
      author: 'MsKingBean89',
      chapters: [
        {
          chapter_uuid: 'uuid-2',
          chapter_id: 'source-2',
          chapter_number: 1,
          chapter_title: '1971',
          source_url: 'https://archiveofourown.org/works/2/chapters/1',
          status: 'COMPLETED',
          master_audio_available: true,
        },
      ],
    },
  ],
};

describe('LibrarySidebar Search and Filtering', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.mocked(apiClient.getLibrary).mockReset().mockResolvedValue(mockLibrary);
    useAppStore.getState().resetChapterState();
    useAppStore.getState().toggleLibraryDrawer(true);
  });

  it('filters fics by title', async () => {
    render(<LibrarySidebar onSelect={vi.fn()} />);
    expect(await screen.findByText('The Debt of Time')).toBeInTheDocument();
    expect(screen.getByText('All the Young Dudes')).toBeInTheDocument();

    const searchInput = screen.getByPlaceholderText(/filter saved fics/i);
    fireEvent.change(searchInput, { target: { value: 'Debt' } });

    expect(screen.getByText('The Debt of Time')).toBeInTheDocument();
    expect(screen.queryByText('All the Young Dudes')).not.toBeInTheDocument();
  });

  it('filters fics by author name', async () => {
    render(<LibrarySidebar onSelect={vi.fn()} />);
    expect(await screen.findByText('The Debt of Time')).toBeInTheDocument();

    const searchInput = screen.getByPlaceholderText(/filter saved fics/i);
    fireEvent.change(searchInput, { target: { value: 'MsKingBean' } });

    expect(screen.getByText('All the Young Dudes')).toBeInTheDocument();
    expect(screen.queryByText('The Debt of Time')).not.toBeInTheDocument();
  });

  it('filters fics by chapter title', async () => {
    render(<LibrarySidebar onSelect={vi.fn()} />);
    expect(await screen.findByText('The Debt of Time')).toBeInTheDocument();

    const searchInput = screen.getByPlaceholderText(/filter saved fics/i);
    fireEvent.change(searchInput, { target: { value: 'Whispering' } });

    expect(screen.getByText('The Debt of Time')).toBeInTheDocument();
    expect(screen.queryByText('All the Young Dudes')).not.toBeInTheDocument();
  });
});

