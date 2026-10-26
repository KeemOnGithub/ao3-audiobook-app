import React, { useEffect, useRef, useMemo } from 'react';
import { Target, FileText } from 'lucide-react';
import { useAppStore } from '../store/useAppStore';
import { groupSegmentsIntoParagraphs } from '../utils/paragraphGrouper';
import { SegmentSpan } from './SegmentSpan';
import { PipelineHero } from './PipelineHero';
import { SpeakerAssignmentControl } from './SpeakerAssignmentControl';

export const ScriptReader: React.FC = () => {
  const containerRef = useRef<HTMLDivElement>(null);
  const activeSegmentRef = useRef<HTMLElement | null>(null);

  const segmentOrders = useAppStore((state) => state.segmentOrders);
  const segmentsByOrder = useAppStore((state) => state.segmentsByOrder);
  const activeSegmentOrder = useAppStore((state) => state.activeSegmentOrder);
  const playbackStatus = useAppStore((state) => state.playbackStatus);
  const autoScrollEnabled = useAppStore((state) => state.autoScrollEnabled);
  const setAutoScrollEnabled = useAppStore((state) => state.setAutoScrollEnabled);
  const metadata = useAppStore((state) => state.metadata);
  const characterIds = useAppStore((state) => state.characterIds);
  const highlighted = useAppStore((state) => state.highlightedCharacterId);
  const readerFont = useAppStore((state) => state.readerFont);
  const readerFontSize = useAppStore((state) => state.readerFontSize);

  // Group into paragraph blocks
  const paragraphBlocks = useMemo(
    () => groupSegmentsIntoParagraphs(segmentOrders, segmentsByOrder),
    [segmentOrders, segmentsByOrder]
  );

  // Auto-scroll when activeSegmentOrder changes
  useEffect(() => {
    if (!autoScrollEnabled) return;

    const el = containerRef.current?.querySelector(
      `[data-segment-order="${activeSegmentOrder}"]`
    ) as HTMLElement | null;

    if (el) {
      activeSegmentRef.current = el;
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, [activeSegmentOrder, autoScrollEnabled]);

  // Detect user manual scroll to disable auto-scroll if drifted > 80px
  const handleScroll = () => {
    if (!autoScrollEnabled || !activeSegmentRef.current || !containerRef.current) return;

    const containerRect = containerRef.current.getBoundingClientRect();
    const activeRect = activeSegmentRef.current.getBoundingClientRect();

    const activeCenter = activeRect.top + activeRect.height / 2;
    const containerCenter = containerRect.top + containerRect.height / 2;

    if (Math.abs(activeCenter - containerCenter) > 120) {
      setAutoScrollEnabled(false);
    }
  };

  const handleResumeAutoScroll = () => {
    setAutoScrollEnabled(true);
    const el = containerRef.current?.querySelector(
      `[data-segment-order="${activeSegmentOrder}"]`
    ) as HTMLElement | null;
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  };

  const totalCharacters = characterIds.length;

  return (
    <div className="relative flex-1 h-full overflow-hidden flex flex-col bg-transparent">
      {metadata?.review_phase === 'AWAITING_APPROVAL' && <SpeakerAssignmentControl />}
      {highlighted && (
        <button
          className="text-xs text-[#ff7a45] hover:text-[#ff5a1f] p-2 bg-[#ff5a1f]/10 border-b border-[#ff5a1f]/20 font-medium transition-colors"
          onClick={() => useAppStore.getState().setHighlightedCharacter(null)}
        >
          Clear Dialogue Highlight (Full Chapter Shown)
        </button>
      )}
      <div
        ref={containerRef}
        onWheel={handleScroll}
        onTouchMove={handleScroll}
        className="flex-1 overflow-y-auto px-6 py-8 md:px-12 md:py-12 relative flex flex-col items-center"
      >
        {paragraphBlocks.length === 0 ? (
          metadata ? (
            <PipelineHero metadata={metadata} />
          ) : (
            <div className="h-full flex flex-col items-center justify-center text-center p-8 text-stone-500 mt-20">
              <div className="p-4 rounded-2xl bg-white/[0.04] border border-white/[0.08] text-stone-400 mb-4">
                <FileText className="w-12 h-12 text-[#ff7a45]" />
              </div>
              <h3 className="text-lg font-bold text-stone-300 font-heading mb-1">
                No Chapter Loaded
              </h3>
              <p className="text-xs text-stone-400 max-w-md">
                Paste an Archive of Our Own (AO3) work or chapter URL in the top header and click Load to begin.
              </p>
            </div>
          )
        ) : (
          <article
            id="story-article"
            className={`w-full max-w-[65ch] space-y-6 pb-40 leading-[1.85] transition-all duration-200 ${
              readerFont === 'serif' ? 'font-story-serif' : 'font-story-sans'
            }`}
            style={{ fontSize: `${readerFontSize}px` }}
          >
            {metadata && (
              <header className="text-center space-y-2 mb-10 pb-6 border-b border-white/[0.08]">
                <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-[#ff5a1f]/10 text-[#ff7a45] text-xs font-semibold">
                  <span>✨ {metadata.work_title || 'Audiobook'} • Chapter {metadata.chapter_number}</span>
                </div>
                <h2 className="text-3xl md:text-4xl font-extrabold tracking-tight font-heading">
                  {metadata.chapter_title}
                </h2>
                <p className="text-xs text-stone-400">
                  {totalCharacters > 0 ? `${totalCharacters} ${totalCharacters === 1 ? 'Character' : 'Characters'} speaking • ` : ''}
                  Voice narration {metadata.ready_segments >= (metadata.total_segments || 0) && (metadata.total_segments || 0) > 0 ? 'ready' : 'in progress'}
                </p>
              </header>
            )}

            {paragraphBlocks.map((block, pIdx) => (
              <p
                key={`para-${block.paragraph_index}-${pIdx}`}
                className="story-paragraph mb-6 leading-[1.85]"
              >
                {block.segments.map((seg, index) => (
                  <React.Fragment key={seg.segment_uuid || `seg-${seg.segment_order}`}>
                    {/* Legacy scripts lack boundary spaces. Separate words, never leading punctuation. */}
                    {index > 0 &&
                      /[\p{L}\p{N}.,!?;:)\]}"']$/u.test(block.segments[index - 1].text) &&
                      /^[\p{L}\p{N}]/u.test(seg.text) && (
                        <span data-reader-text>{' '}</span>
                      )}
                    <SegmentSpan
                      segment={seg}
                      isActive={activeSegmentOrder === seg.segment_order}
                      playbackStatus={playbackStatus}
                    />
                  </React.Fragment>
                ))}
              </p>
            ))}
          </article>
        )}
      </div>

      {/* Floating Pill: Resume Auto-Scroll */}
      {!autoScrollEnabled && paragraphBlocks.length > 0 && (
        <div className="absolute bottom-24 left-1/2 -translate-x-1/2 z-20 animate-in fade-in slide-in-from-bottom-2 duration-150">
          <button
            onClick={handleResumeAutoScroll}
            className="flex items-center gap-2 px-4 py-2 bg-[#ff5a1f]/90 hover:bg-[#ff5a1f] text-white text-xs font-semibold rounded-full shadow-xl shadow-[#ff5a1f]/30 backdrop-blur-md border border-[#ff5a1f]/40 transition-all cursor-pointer hover:scale-105 active:scale-95"
          >
            <Target className="w-4 h-4 text-white" />
            <span>Resume Auto-Scroll</span>
          </button>
        </div>
      )}
    </div>
  );
};
