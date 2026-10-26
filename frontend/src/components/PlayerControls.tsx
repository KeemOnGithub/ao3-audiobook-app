import React, { useEffect } from 'react';
import {
  Play,
  Pause,
  RotateCcw,
  RotateCw,
  Loader2,
} from 'lucide-react';
import { useAppStore } from '../store/useAppStore';
import { gaplessAudioEngine } from '../services/GaplessAudioEngine';
import { PlaybackSpeed } from '../types/domain';

const SPEED_OPTIONS: PlaybackSpeed[] = [0.75, 1.0, 1.25, 1.5, 2.0];

export const PlayerControls: React.FC = () => {
  const playbackStatus = useAppStore((state) => state.playbackStatus);
  const activeSegmentOrder = useAppStore((state) => state.activeSegmentOrder);
  const playbackSpeed = useAppStore((state) => state.playbackSpeed);
  const metadata = useAppStore((state) => state.metadata);
  const segmentOrders = useAppStore((state) => state.segmentOrders);
  const segmentsByOrder = useAppStore((state) => state.segmentsByOrder);
  const authorized = metadata?.narration_authorized === true;

  const totalSegments = metadata?.total_segments || segmentOrders.length || 0;
  const readySegments = metadata?.ready_segments || 0;

  const isPlaying =
    playbackStatus === 'PLAYING' || playbackStatus === 'INTER_SEGMENT_PAUSE';
  const isWaiting = playbackStatus === 'WAITING_FOR_TTS';
  const isPauseable = isPlaying || isWaiting;

  const currentSegment = segmentsByOrder[activeSegmentOrder];
  const speakerName = currentSegment?.speaker_name || 'Narrator';

  // Toggle Play / Pause per Spec 07 Section 11.2 (waiting intent is pauseable)
  const handleTogglePlay = () => {
    if (!authorized) return;
    if (isPauseable) {
      gaplessAudioEngine.pause();
    } else {
      gaplessAudioEngine.play();
    }
  };

  // Keyboard Shortcuts per Spec 06 section 7.4
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (!authorized || (e.target as HTMLElement).closest('[role="dialog"],dialog,button,select')) return;
      // Ignore if typing in input or textarea
      const target = e.target as HTMLElement;
      if (
        target.tagName === 'INPUT' ||
        target.tagName === 'TEXTAREA' ||
        target.isContentEditable
      ) {
        return;
      }

      if (e.code === 'Space') {
        e.preventDefault();
        handleTogglePlay();
      } else if (e.code === 'ArrowLeft') {
        e.preventDefault();
        gaplessAudioEngine.skipBySeconds(-10);
      } else if (e.code === 'ArrowRight') {
        e.preventDefault();
        gaplessAudioEngine.skipBySeconds(10);
      } else if (e.altKey && e.code === 'ArrowUp') {
        e.preventDefault();
        if (activeSegmentOrder > 0) {
          gaplessAudioEngine.seekToSegment(activeSegmentOrder - 1);
        }
      } else if (e.altKey && e.code === 'ArrowDown') {
        e.preventDefault();
        if (activeSegmentOrder < totalSegments - 1) {
          gaplessAudioEngine.seekToSegment(activeSegmentOrder + 1);
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [authorized, isPauseable, activeSegmentOrder, totalSegments]);

  // Buffer and progress percentages
  const bufferPercent =
    totalSegments > 0
      ? Math.min(100, Math.round((readySegments / totalSegments) * 100))
      : 0;

  const playheadPercent =
    totalSegments > 0
      ? Math.min(100, Math.round(((activeSegmentOrder + 1) / totalSegments) * 100))
      : 0;

  const handleProgressBarClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!authorized || totalSegments === 0) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const clickX = e.clientX - rect.left;
    const ratio = Math.max(0, Math.min(1, clickX / rect.width));
    const targetOrder = Math.floor(ratio * totalSegments);
    gaplessAudioEngine.seekToSegment(targetOrder);
  };

  const handleCycleSpeed = () => {
    const currentIndex = SPEED_OPTIONS.indexOf(playbackSpeed);
    const nextIndex = (currentIndex + 1) % SPEED_OPTIONS.length;
    gaplessAudioEngine.setSpeed(SPEED_OPTIONS[nextIndex]);
  };

  return (
    <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-40 w-[92%] max-w-xl pointer-events-auto">
      <div className="rounded-3xl bg-[#191a20]/95 border border-white/10 px-5 py-3 shadow-[0_20px_50px_rgba(0,0,0,0.6)] backdrop-blur-2xl flex items-center justify-between gap-4">
        {/* Left: Speaking Character Pill */}
        <div className="flex items-center gap-2.5 shrink-0 min-w-[110px] max-w-[140px]">
          {isPlaying && (
            <div id="playing-wave" className="flex items-end gap-0.5 h-4 w-3.5 shrink-0" aria-hidden="true">
              <span className="w-1 bg-[#ff5a1f] rounded-full wave-1"></span>
              <span className="w-1 bg-[#ff5a1f] rounded-full wave-2"></span>
              <span className="w-1 bg-[#ff5a1f] rounded-full wave-3"></span>
            </div>
          )}
          <div className="min-w-0">
            <p id="player-speaker" className="text-xs font-bold text-[#ff7a45] truncate" title={speakerName}>
              {speakerName}
            </p>
            <p id="player-timecode" className="text-[10px] text-stone-400 font-medium truncate">
              Seg {totalSegments > 0 ? activeSegmentOrder + 1 : 0}/{totalSegments}
            </p>
          </div>
        </div>

        {/* Center: Transport Buttons */}
        <div className="flex items-center gap-2 shrink-0">
          <button
            id="skip-back-btn"
            onClick={() => gaplessAudioEngine.skipBySeconds(-10)}
            disabled={!authorized}
            title="Skip back 10 seconds (Left Arrow)"
            className="p-2 text-stone-400 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed hover:scale-105 active:scale-95 transition-all cursor-pointer"
          >
            <RotateCcw className="w-4 h-4" />
          </button>

          {/* Big Tangerine Play/Pause Button */}
          <button
            id="master-play-btn"
            onClick={handleTogglePlay}
            disabled={!authorized}
            title={isPauseable ? 'Pause (Space)' : 'Play (Space)'}
            className="w-11 h-11 rounded-full bg-[#ff5a1f] hover:bg-[#ff7038] disabled:bg-stone-800 disabled:text-stone-500 disabled:shadow-none text-white flex items-center justify-center shadow-lg shadow-[#ff5a1f]/30 active:scale-95 transition-all cursor-pointer disabled:cursor-not-allowed shrink-0"
          >
            {isWaiting ? (
              <Loader2 className="w-5 h-5 animate-spin text-amber-300" />
            ) : isPlaying ? (
              <Pause className="w-5 h-5 fill-current" />
            ) : (
              <Play className="w-5 h-5 fill-current ml-0.5" />
            )}
          </button>

          <button
            id="skip-fwd-btn"
            onClick={() => gaplessAudioEngine.skipBySeconds(10)}
            disabled={!authorized}
            title="Skip forward 10 seconds (Right Arrow)"
            className="p-2 text-stone-400 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed hover:scale-105 active:scale-95 transition-all cursor-pointer"
          >
            <RotateCw className="w-4 h-4" />
          </button>
        </div>

        {/* Right: Scrubber & Speed */}
        <div className="flex-1 flex items-center gap-2.5 min-w-0">
          <div
            id="progress-container"
            onClick={handleProgressBarClick}
            title="Click to seek"
            className="group relative flex-1 h-2 bg-white/10 hover:h-2.5 rounded-full cursor-pointer transition-all overflow-hidden"
          >
            {/* Buffer ribbon */}
            <div
              className="absolute inset-y-0 left-0 bg-emerald-500/25 transition-all duration-300"
              style={{ width: `${bufferPercent}%` }}
            />
            {/* Playhead ribbon */}
            <div
              id="progress-bar"
              className="h-full bg-gradient-to-r from-[#ff5a1f] to-[#ff8c42] rounded-full transition-all duration-150 shadow-sm"
              style={{ width: `${playheadPercent}%` }}
            />
          </div>

          <button
            id="speed-btn"
            onClick={handleCycleSpeed}
            title="Cycle playback speed"
            className="px-2 py-1 rounded-lg text-xs font-bold font-mono text-stone-300 hover:text-white hover:bg-white/[0.08] transition-colors shrink-0"
          >
            {playbackSpeed.toFixed(2)}x
          </button>
        </div>
      </div>
    </div>
  );
};
