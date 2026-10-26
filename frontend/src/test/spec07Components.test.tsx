import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { useAppStore } from '../store/useAppStore';
import { SegmentSpan } from '../components/SegmentSpan';
import { VoiceSidebar } from '../components/VoiceSidebar';
import { Character, ScriptSegment } from '../types/domain';

describe('Spec 07 Component Presentations', () => {
  beforeEach(() => {
    useAppStore.getState().resetChapterState();
  });

  describe('SegmentSpan failure and waiting states', () => {
    it('suppresses waiting spinner on failed dialogue segment and displays error indicator', () => {
      const failedDialogue: ScriptSegment = {
        segment_uuid: 'seg_failed_dial',
        segment_order: 0,
        chunk_index: 0,
        paragraph_index: 0,
        is_paragraph_end: false,
        speaker_uuid: 'char_alice',
        speaker_name: 'Alice',
        is_dialogue: true,
        text: 'This speech failed synthesis.',
        audio_status: 'ERROR',
        audio_url: null,
        duration_ms: null,
        voice_version: 1,
        error_message: 'Remote TTS synthesis failed',
      };

      // Even when playback status is WAITING_FOR_TTS, failed segment MUST NOT show waiting spinner
      render(
        <SegmentSpan
          segment={failedDialogue}
          isActive={true}
          playbackStatus="WAITING_FOR_TTS"
        />
      );

      // Should display TTS Error
      expect(screen.getByText('TTS Error')).toBeInTheDocument();
      // Should NOT show "Synthesizing audio..."
      expect(screen.queryByText(/Synthesizing audio/i)).toBeNull();
    });

    it('suppresses waiting spinner on failed narrator segment and displays error indicator', () => {
      const failedNarrator: ScriptSegment = {
        segment_uuid: 'seg_failed_narr',
        segment_order: 1,
        chunk_index: 0,
        paragraph_index: 0,
        is_paragraph_end: true,
        speaker_uuid: 'char_narrator',
        speaker_name: 'Narrator',
        is_dialogue: false,
        text: 'The narrator description failed.',
        audio_status: 'ERROR',
        audio_url: null,
        duration_ms: null,
        voice_version: 1,
      };

      render(
        <SegmentSpan
          segment={failedNarrator}
          isActive={true}
          playbackStatus="WAITING_FOR_TTS"
        />
      );

      expect(screen.getByText('TTS Error')).toBeInTheDocument();
      expect(screen.queryByText(/Synthesizing audio/i)).toBeNull();
    });
  });

  describe('VoiceSidebar character status handling', () => {
    it('renders READY with preview, READY without preview, PENDING, GENERATING, and FAILED states', () => {
      const characters: Character[] = [
        {
          character_uuid: 'char_ready_preview',
          canonical_name: 'Ready With Preview',
          aliases: [],
          is_narrator: false,
          voice_instruct: 'Friendly voice',
          voice_status: 'READY',
          voice_version: 1,
          preview_url: '/audio/preview1.mp3',
          segment_count: 5,
        },
        {
          character_uuid: 'char_ready_no_preview',
          canonical_name: 'Ready No Preview',
          aliases: [],
          is_narrator: false,
          voice_instruct: 'Voice without preview file',
          voice_status: 'READY',
          preview_url: null,
          voice_version: 1,
          segment_count: 3,
        },
        {
          character_uuid: 'char_pending',
          canonical_name: 'Pending Character',
          aliases: [],
          is_narrator: false,
          voice_instruct: 'Pending',
          voice_status: 'PENDING',
          preview_url: null,
          voice_version: 1,
          segment_count: 2,
        },
        {
          character_uuid: 'char_generating',
          canonical_name: 'Generating Character',
          aliases: [],
          is_narrator: false,
          voice_instruct: 'Generating',
          voice_status: 'GENERATING',
          preview_url: null,
          voice_version: 1,
          segment_count: 1,
        },
        {
          character_uuid: 'char_failed',
          canonical_name: 'Failed Character',
          aliases: [],
          is_narrator: false,
          voice_instruct: 'Failed voice',
          voice_status: 'FAILED',
          preview_url: null,
          voice_version: 1,
          segment_count: 1,
        },
      ];

      useAppStore.getState().upsertCharacters(characters);
      render(<VoiceSidebar />);

      // Button for READY with preview should be enabled
      const playBtn = screen.getByRole('button',{name:'Listen to applied Ready With Preview voice'});
      expect(playBtn).toBeEnabled();

      // Button for READY without preview should be disabled with 'Preview unavailable'
      expect(screen.getByText('Preview unavailable')).toBeInTheDocument();
      expect(screen.queryByRole('button',{name:'Listen to applied Ready No Preview voice'})).not.toBeInTheDocument();

      // Button for PENDING should be disabled with 'Voice queued...'
      expect(screen.queryByRole('button',{name:'Listen to applied Pending Character voice'})).not.toBeInTheDocument();
      expect(screen.getAllByText(/Untested default/).length).toBeGreaterThan(0);

      // Button for GENERATING should be disabled with 'Generating voice preview...'
      expect(screen.getByText('Generating approved default voice')).toBeInTheDocument();
      expect(screen.queryByRole('button',{name:'Listen to applied Generating Character voice'})).not.toBeInTheDocument();

      // Button for FAILED should be disabled with 'Voice generation failed'
      expect(screen.getByText('Voice generation failed')).toBeInTheDocument();
      expect(screen.queryByRole('button',{name:'Listen to applied Failed Character voice'})).not.toBeInTheDocument();
    });
  });
});

