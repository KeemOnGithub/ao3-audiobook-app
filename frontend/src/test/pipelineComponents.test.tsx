import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { useAppStore } from '../store/useAppStore';
import { PipelineHero } from '../components/PipelineHero';
import { PipelineDrawer } from '../components/PipelineDrawer';
import { WorkMetadata } from '../types/domain';

const dummyMetadata: WorkMetadata = {
  work_id: 'work_123',
  chapter_id: 'chap_1',
  source_url: 'https://archiveofourown.org/works/123/chapters/1',
  work_title: 'Pride and Prejudices',
  chapter_title: 'Chapter 1: The Encounter',
  chapter_number: 1,
  total_chapters: 5,
  author: 'Jane Austen',
  status: 'SCRIPTING_CHUNKS',
  total_chunks: 4,
  scripted_chunks: 1,
  total_segments: 20,
  ready_segments: 5,
  master_mp3_progress: 0,
  master_mp3_url: null,
  error_message: null,
};

describe('Pipeline Components', () => {
  beforeEach(() => {
    useAppStore.getState().resetWork();
  });

  describe('PipelineHero', () => {
    it('renders chapter metadata and 3-step pipeline progress', () => {
      useAppStore.getState().setMetadata(dummyMetadata);
      useAppStore.getState().setPipelineProgress({
        stage: 'ANALYZING_SCRIPT',
        currentChunk: 2,
        totalChunks: 4,
        totalSegments: 20,
        readySegments: 5,
      });

      render(<PipelineHero />);

      expect(screen.getByText('Chapter 1: The Encounter')).toBeInTheDocument();
      expect(screen.getByText('Pride and Prejudices')).toBeInTheDocument();
      expect(screen.getByText('Jane Austen')).toBeInTheDocument();
      expect(screen.getByText('1. Ingest Chapter Text')).toBeInTheDocument();
      expect(screen.getByText('2. Screenplay & Character Extraction')).toBeInTheDocument();
      expect(screen.getByText('3. Voice Synthesis (TTS)')).toBeInTheDocument();
      expect(screen.getByText(/Chunk 2 of 4/i)).toBeInTheDocument();
    });

    it('returns null when no metadata exists', () => {
      const { container } = render(<PipelineHero />);
      expect(container).toBeEmptyDOMElement();
    });
  });

  describe('PipelineDrawer', () => {
    it('renders collapsed metrics and expands on click', () => {
      useAppStore.getState().setMetadata(dummyMetadata);
      useAppStore.getState().setPipelineProgress({
        stage: 'SYNTHESIZING_AUDIO',
        currentChunk: 2,
        totalChunks: 4,
        totalSegments: 20,
        readySegments: 5,
        isDrawerExpanded: false,
      });

      render(<PipelineDrawer />);

      // In collapsed mode
      expect(screen.getByText(/LLM 1\/4/i)).toBeInTheDocument();
      expect(screen.getByText(/TTS 5\/20 \(25%\)/i)).toBeInTheDocument();
      expect(screen.queryByText('Pipeline Breakdown')).not.toBeInTheDocument();

      // Click to expand
      fireEvent.click(screen.getByTitle('Click to expand pipeline status'));
      expect(useAppStore.getState().pipeline.isDrawerExpanded).toBe(true);

      // Re-render / assert expanded elements
      expect(screen.getByText('Pipeline Breakdown')).toBeInTheDocument();
      expect(screen.getByText('LLM Screenplay Parsing')).toBeInTheDocument();
      expect(screen.getByText('TTS Voice Synthesis')).toBeInTheDocument();
    });

    it('shows completed badge when synthesis is finished', () => {
      useAppStore.getState().setMetadata({
        ...dummyMetadata,
        status: 'COMPLETED',
        review_phase:'COMPLETED',narration_authorized:true,
        master_mp3_progress: 100,
        master_mp3_url: '/audio/master.mp3',
      });
      useAppStore.getState().setPipelineProgress({
        stage: 'COMPLETED',
        currentChunk: 4,
        totalChunks: 4,
        totalSegments: 20,
        readySegments: 20,
        isDrawerExpanded: false,
      });

      render(<PipelineDrawer />);
      expect(screen.getByText(/All Audio Ready • 20 Segments/i)).toBeInTheDocument();
    });
  });
});
