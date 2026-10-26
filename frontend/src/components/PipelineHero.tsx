import React from 'react';
import {
  BookOpen,
  Cpu,
  Mic,
  CheckCircle2,
  Loader2,
  Sparkles,
} from 'lucide-react';
import { useAppStore } from '../store/useAppStore';
import { WorkMetadata } from '../types/domain';

interface PipelineHeroProps {
  metadata?: WorkMetadata | null;
}

export const PipelineHero: React.FC<PipelineHeroProps> = ({ metadata: propMetadata }) => {
  const storeMetadata = useAppStore((state) => state.metadata);
  const pipeline = useAppStore((state) => state.pipeline);
  const autoplayEnabled = useAppStore((state) => state.autoplayEnabled);

  const metadata = propMetadata || storeMetadata;
  if (!metadata) return null;

  const totalChunks = pipeline.totalChunks || metadata.total_chunks || 1;
  const currentChunk = Math.max(1, Math.min(pipeline.currentChunk || 1, totalChunks));
  const chunkPercent = Math.round((currentChunk / totalChunks) * 100);

  return (
    <div className="flex flex-col items-center justify-center min-h-[500px] w-full px-4 py-8 animate-in fade-in duration-300">
      <div className="w-full max-w-2xl bg-[#171820]/95 border border-white/[0.08] rounded-3xl p-6 sm:p-8 shadow-[0_20px_50px_rgba(0,0,0,0.5)] backdrop-blur-xl">
        {/* Top Active Status Badge & Chapter Badges */}
        <div className="flex items-center justify-between gap-3 mb-6">
          <div className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full bg-[#ff5a1f]/15 border border-[#ff5a1f]/30 text-[#ff7a45] text-xs font-semibold">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-[#ff5a1f] opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-[#ff5a1f]"></span>
            </span>
            <span>Chapter Loaded • Deconstructing Script &amp; Cast</span>
          </div>

          <div className="flex items-center gap-1.5 text-xs text-stone-300 font-mono">
            <span className="px-2.5 py-1 rounded-xl bg-white/[0.06] border border-white/10 font-semibold">
              Ch. {metadata.chapter_number}
            </span>
            <span className="px-2.5 py-1 rounded-xl bg-white/[0.06] border border-white/10 font-semibold">
              {totalChunks} {totalChunks === 1 ? 'Chunk' : 'Chunks'}
            </span>
          </div>
        </div>

        {/* Work & Chapter Title Header */}
        <div className="mb-8">
          <div className="flex items-start gap-3.5">
            <div className="w-10 h-10 rounded-2xl bg-gradient-to-tr from-[#ff5a1f] to-[#ff8c42] flex items-center justify-center text-white shadow-[0_4px_16px_rgba(255,90,31,0.35)] shrink-0 mt-0.5">
              <BookOpen className="w-5 h-5 fill-current" />
            </div>
            <div className="min-w-0">
              <h2 className="text-xl sm:text-2xl font-extrabold text-white font-heading tracking-tight leading-snug">
                {metadata.chapter_title}
              </h2>
              <p className="text-sm text-[#ff7a45] font-semibold mt-0.5">
                {metadata.work_title}
              </p>
              <p className="text-xs text-stone-400 mt-0.5">
                by <span className="text-stone-300 font-medium">{metadata.author}</span>
              </p>
            </div>
          </div>
        </div>

        {/* 3-Step Multi-Stage Pipeline Stepper */}
        <div className="space-y-3.5">
          <h4 className="text-[11px] font-bold uppercase tracking-wider text-stone-400 mb-2 font-heading">
            Pipeline Progression
          </h4>

          {/* Step 1: Ingestion */}
          <div className="flex items-start gap-4 p-4 rounded-2xl bg-emerald-950/20 border border-emerald-500/20 transition-all">
            <div className="p-2 rounded-xl bg-emerald-500/20 text-emerald-400 shrink-0 mt-0.5">
              <CheckCircle2 className="w-4 h-4" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-bold text-emerald-300">
                  1. Ingest Chapter Text
                </span>
                <span className="text-[11px] font-medium text-emerald-400 bg-emerald-950/80 px-2.5 py-0.5 rounded-full border border-emerald-500/30">
                  Completed
                </span>
              </div>
              <p className="text-xs text-stone-400 mt-1 leading-relaxed">
                Scraped from AO3 and partitioned into {totalChunks} optimized text chunk{totalChunks === 1 ? '' : 's'}.
              </p>
            </div>
          </div>

          {/* Step 2: Screenplay & Character Extraction (Active) */}
          <div className="flex flex-col gap-3 p-4 rounded-2xl bg-[#ff5a1f]/10 border border-[#ff5a1f]/35 shadow-lg shadow-[#ff5a1f]/5 transition-all">
            <div className="flex items-start gap-4">
              <div className="p-2 rounded-xl bg-[#ff5a1f]/20 text-[#ff7a45] shrink-0 mt-0.5 animate-pulse">
                <Cpu className="w-4 h-4" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-bold text-[#ffebe3] flex items-center gap-2">
                    2. Screenplay &amp; Character Extraction
                  </span>
                  <span className="text-[11px] font-semibold text-[#ff7a45] bg-[#ff5a1f]/20 px-2.5 py-0.5 rounded-full border border-[#ff5a1f]/30 flex items-center gap-1.5">
                    <Loader2 className="w-3 h-3 animate-spin text-[#ff7a45]" />
                    <span>Chunk {currentChunk} of {totalChunks}</span>
                  </span>
                </div>
                <p className="text-xs text-stone-300 mt-1 leading-relaxed">
                  Qwen LLM is isolating dialogue tags, emotions, and character voices.
                </p>
              </div>
            </div>

            {/* Chunk Progress Bar */}
            <div className="w-full bg-black/40 rounded-full h-2 overflow-hidden border border-white/10 mt-1">
              <div
                className="bg-gradient-to-r from-[#ff5a1f] to-[#ff8c42] h-full rounded-full transition-all duration-500 ease-out shadow-sm"
                style={{ width: `${Math.max(15, chunkPercent)}%` }}
              />
            </div>
          </div>

          {/* Step 3: Voice Synthesis */}
          <div className="flex items-start gap-4 p-4 rounded-2xl bg-white/[0.03] border border-white/[0.06] opacity-75">
            <div className="p-2 rounded-xl bg-white/[0.06] border border-white/10 text-stone-400 shrink-0 mt-0.5">
              <Mic className="w-4 h-4" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-semibold text-stone-300">
                  3. Voice Synthesis (TTS)
                </span>
                <span className="text-[11px] font-medium text-stone-400 bg-white/[0.06] px-2.5 py-0.5 rounded-full border border-white/10">
                  Queued
                </span>
              </div>
              <p className="text-xs text-stone-500 mt-1">
                Ready to stream synthesis upon arrival of the first segment.
              </p>
            </div>
          </div>
        </div>

        {/* Informative Reassurance Footer */}
        <div className="mt-6 pt-5 border-t border-white/[0.08] flex items-center gap-3 text-xs text-stone-400">
          <div className="p-1.5 rounded-xl bg-[#ff5a1f]/15 text-[#ff7a45] shrink-0">
            <Sparkles className="w-4 h-4" />
          </div>
          <p className="leading-relaxed">
            {autoplayEnabled ? (
              <>
                <strong className="text-stone-200 font-semibold">Autoplay is on:</strong> Playback will begin smoothly as soon as the first chunk finishes processing.
              </>
            ) : (
              <>
                <strong className="text-stone-200 font-semibold">Click to Play is on:</strong> Audio will buffer silently and be ready whenever you press Play.
              </>
            )}
          </p>
        </div>
      </div>
    </div>
  );
};
