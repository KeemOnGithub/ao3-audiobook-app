import React from 'react';
import { Loader2, AlertCircle, Sparkles } from 'lucide-react';
import { PlaybackStatus, ScriptSegment } from '../types/domain';
import { getCharacterTheme } from '../utils/colorHash';
import { gaplessAudioEngine } from '../services/GaplessAudioEngine';
import { useAppStore } from '../store/useAppStore';

interface SegmentSpanProps {
  segment: ScriptSegment;
  isActive: boolean;
  playbackStatus: PlaybackStatus;
}

export const SegmentSpan: React.FC<SegmentSpanProps> = React.memo(
  ({ segment, isActive, playbackStatus }) => {
    const editable = useAppStore((s) => s.metadata?.review_phase === 'AWAITING_APPROVAL');
    const authorized = useAppStore((s) => s.metadata?.narration_authorized === true);
    const selected = useAppStore((s) => s.selectedSegmentIds.includes(segment.segment_uuid));
    const highlighted = useAppStore((s) => s.highlightedCharacterId === segment.speaker_uuid);
    const isError = segment.audio_status === 'ERROR';
    const isPlaying = isActive && playbackStatus === 'PLAYING';
    const isWaiting = isActive && playbackStatus === 'WAITING_FOR_TTS' && !isError;
    const isStale = segment.audio_status === 'STALE_VOICE';
    const isNotReady =
      (segment.audio_status === 'PENDING_TTS' || segment.audio_status === 'SYNTHESIZING') && !isError;

    const theme = getCharacterTheme(segment.speaker_uuid, !segment.is_dialogue);

    const handleClick = () => {
      if (!authorized) return;
      gaplessAudioEngine.seekToSegment(segment.segment_order);
    };

    // Active state highlighting - notice: NEVER include \bpx- in activeClasses or data-segment-order/data-reader-text
    const activeClasses = isPlaying
      ? 'active-segment py-0.5'
      : isError && isActive
      ? 'ring-2 ring-red-500/80 bg-red-950/40 rounded py-0.5'
      : isWaiting
      ? 'ring-2 ring-amber-400/80 bg-amber-950/40 rounded py-0.5 animate-pulse'
      : isStale
      ? 'border border-dashed border-amber-600/40 opacity-70 rounded'
      : isNotReady
      ? 'border-b border-dotted border-stone-600 hover:border-stone-400 cursor-pointer'
      : 'hover:bg-white/[0.06] rounded cursor-pointer transition-colors';

    return (
      <span
        data-segment-order={segment.segment_order}
        onClick={handleClick}
        className={`inline transition-all duration-150 relative ${activeClasses} ${
          highlighted ? 'bg-amber-900/40 ring-1 ring-amber-400' : ''
        }`}
        title={`${authorized ? 'Click to seek to' : 'Review'} segment ${segment.segment_order + 1} (${segment.speaker_name})`}
      >
        {editable && (
          <input
            type="checkbox"
            aria-label={`Select segment ${segment.segment_order + 1}: ${segment.speaker_name}`}
            checked={selected}
            onClick={(e) => e.stopPropagation()}
            onChange={() => useAppStore.getState().toggleSegmentSelection(segment.segment_uuid)}
            className="mx-1 align-middle accent-[#ff5a1f] rounded cursor-pointer"
          />
        )}
        {segment.is_dialogue ? (
          <>
            {/* Approachable Dialogue Badge with Initial Chip */}
            <span
              className={`inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full border align-middle mx-1 select-none shadow-sm ${theme.badgeBg} ${theme.badgeText} ${theme.badgeBorder}`}
            >
              {isPlaying ? (
                <span className="flex items-center gap-0.5" aria-hidden="true">
                  <span className="w-1 h-2.5 bg-[#ff5a1f] animate-pulse rounded-full" />
                  <span className="w-1 h-3.5 bg-[#ff7a45] animate-pulse delay-75 rounded-full" />
                  <span className="w-1 h-2 bg-[#ff5a1f] animate-pulse delay-150 rounded-full" />
                </span>
              ) : isError ? (
                <AlertCircle className="w-3 h-3 text-red-400" />
              ) : isWaiting ? (
                <Loader2 className="w-3 h-3 animate-spin text-amber-400" />
              ) : (
                <span className="w-3.5 h-3.5 rounded-full bg-white/20 text-center text-[9px] font-bold leading-3.5 inline-block">
                  {segment.speaker_name ? segment.speaker_name[0].toUpperCase() : '🎙'}
                </span>
              )}
              <span>{segment.speaker_name}</span>
            </span>

            {/* Spoken Quote */}
            <span
              data-reader-text
              className={`rounded font-medium ${theme.quoteText} ${
                isPlaying ? 'text-white' : ''
              }`}
            >
              {segment.text}
            </span>
          </>
        ) : (
          /* Narrator Inline Prose */
          <span
            data-reader-text
            className={isPlaying ? 'text-white font-medium' : isError ? 'text-red-300' : 'text-stone-300'}
          >
            {segment.text}
          </span>
        )}

        {/* Status Micro-Badges */}
        {isWaiting && (
          <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-amber-300 bg-amber-950/80 border border-amber-500/40 px-1.5 py-0.5 rounded ml-1 align-middle">
            <Loader2 className="w-2.5 h-2.5 animate-spin" />
            Synthesizing audio...
          </span>
        )}
        {isStale && !isWaiting && (
          <span className="inline-flex items-center gap-1 text-[10px] font-medium text-amber-400 bg-amber-950/40 border border-amber-600/30 px-1 rounded ml-1 align-middle">
            <Sparkles className="w-2.5 h-2.5" />
            Updating voice...
          </span>
        )}
        {isError && (
          <span className="inline-flex items-center gap-1 text-[10px] font-medium text-red-400 bg-red-950/60 border border-red-700/50 px-1 rounded ml-1 align-middle">
            <AlertCircle className="w-2.5 h-2.5" />
            TTS Error
          </span>
        )}
      </span>
    );
  }
);
