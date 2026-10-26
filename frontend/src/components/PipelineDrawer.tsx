import React from 'react';
import {
  ChevronDown,
  ChevronUp,
  Cpu,
  Mic,
  CheckCircle2,
  AlertCircle,
  Download,
  Loader2,
  Sparkles,
} from 'lucide-react';
import { useAppStore } from '../store/useAppStore';
import { apiClient } from '../services/apiClient';

export const PipelineDrawer: React.FC = () => {
  const metadata = useAppStore((state) => state.metadata);
  const pipeline = useAppStore((state) => state.pipeline);
  const togglePipelineDrawer = useAppStore((state) => state.togglePipelineDrawer);

  if (!metadata) return null;

  const totalChunks = pipeline.totalChunks || metadata.total_chunks || 1;
  const scriptedChunks = metadata.scripted_chunks ?? (pipeline.currentChunk ? pipeline.currentChunk - 1 : 0);
  const clampedScripted = Math.min(totalChunks, Math.max(0, scriptedChunks));

  const totalSegments = pipeline.totalSegments || metadata.total_segments || 0;
  const readySegments = pipeline.readySegments || metadata.ready_segments || 0;

  const llmPercent = totalChunks > 0 ? Math.round((clampedScripted / totalChunks) * 100) : 0;
  const ttsPercent = totalSegments > 0 ? Math.round((readySegments / totalSegments) * 100) : 0;

  const isCompleted = metadata.review_phase === 'COMPLETED';

  const isMasterReady =
    metadata.narration_authorized === true && Boolean(metadata.master_mp3_url);

  const masterDownloadUrl = metadata.chapter_id
    ? apiClient.getMasterDownloadUrl(metadata.chapter_id)
    : metadata.master_mp3_url || '#';

  const isExpanded = pipeline.isDrawerExpanded;

  return (
    <div className="shrink-0 border-t border-white/[0.08] bg-[#16171d]/95 backdrop-blur-md transition-all duration-300 ease-in-out shadow-xl">
      {/* Collapsed Pill Strip / Header */}
      <div
        onClick={() => togglePipelineDrawer()}
        className="px-4 py-2.5 flex items-center justify-between cursor-pointer hover:bg-white/[0.04] select-none transition-colors"
        title={isExpanded ? 'Click to collapse pipeline details' : 'Click to expand pipeline status'}
      >
        <div className="flex items-center gap-2 truncate text-xs">
          {metadata.review_phase === 'AWAITING_APPROVAL' ? (
            <span className="text-[#ff7a45] font-semibold">Awaiting Review &amp; Approval</span>
          ) : isCompleted ? (
            <div className="flex items-center gap-1.5 text-emerald-400 font-semibold truncate">
              <CheckCircle2 className="w-3.5 h-3.5 shrink-0" />
              <span className="truncate">All Audio Ready • {readySegments} Segments</span>
            </div>
          ) : (
            <div className="flex items-center gap-2 text-stone-300 font-medium truncate">
              <span className="flex items-center gap-1 text-[#ff7a45] shrink-0">
                <Cpu className="w-3 h-3" />
                <span>LLM {clampedScripted}/{totalChunks}</span>
              </span>
              <span className="text-stone-600">•</span>
              <span className="flex items-center gap-1 text-emerald-400 shrink-0">
                <Mic className="w-3 h-3" />
                <span>TTS {readySegments}/{totalSegments || '...'} ({ttsPercent}%)</span>
              </span>
            </div>
          )}
        </div>

        <button
          type="button"
          aria-label={isExpanded ? 'Collapse' : 'Expand'}
          className="p-1 rounded-lg text-stone-400 hover:text-white hover:bg-white/[0.08] transition-colors shrink-0 cursor-pointer"
        >
          {isExpanded ? (
            <ChevronDown className="w-4 h-4" />
          ) : (
            <ChevronUp className="w-4 h-4" />
          )}
        </button>
      </div>

      {/* Expanded Details Body */}
      {isExpanded && (
        <div className="px-4 pb-4 pt-1 space-y-3.5 border-t border-white/[0.08] animate-in fade-in duration-200">
          {/* Header Subtitle */}
          <div className="flex items-center justify-between text-[11px] text-stone-400 font-bold uppercase tracking-wider font-heading">
            <span>Pipeline Breakdown</span>
            {metadata.error_message && (
              <span className="text-red-400 normal-case flex items-center gap-1">
                <AlertCircle className="w-3 h-3" /> Error
              </span>
            )}
          </div>

          {/* 1. LLM Screenplay Deconstruction Bar */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between text-xs">
              <span className="text-stone-300 flex items-center gap-1.5 font-medium">
                <Cpu className="w-3.5 h-3.5 text-[#ff7a45]" />
                <span>LLM Screenplay Parsing</span>
              </span>
              <span className="font-mono text-stone-400 text-[11px]">
                {clampedScripted} / {totalChunks} chunks ({llmPercent}%)
              </span>
            </div>
            <div className="w-full bg-black/40 rounded-full h-1.5 overflow-hidden border border-white/10">
              <div
                className="bg-[#ff5a1f] h-full rounded-full transition-all duration-300"
                style={{ width: `${llmPercent}%` }}
              />
            </div>
          </div>

          {/* 2. TTS Voice Synthesis Bar */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between text-xs">
              <span className="text-stone-300 flex items-center gap-1.5 font-medium">
                <Mic className="w-3.5 h-3.5 text-emerald-400" />
                <span>TTS Voice Synthesis</span>
              </span>
              <span className="font-mono text-stone-400 text-[11px]">
                {readySegments} / {totalSegments} ({ttsPercent}%)
              </span>
            </div>
            <div className="w-full bg-black/40 rounded-full h-1.5 overflow-hidden border border-white/10">
              <div
                className="bg-emerald-500 h-full rounded-full transition-all duration-300"
                style={{ width: `${ttsPercent}%` }}
              />
            </div>
          </div>

          {/* 3. Mastering Status / Action */}
          <div className="pt-2 border-t border-white/[0.08] flex items-center justify-between gap-2 text-xs">
            <div className="text-stone-400 text-[11px] flex items-center gap-1.5 truncate">
              {isMasterReady ? (
                <>
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
                  <span className="text-stone-200 font-medium truncate">Master MP3 synthesized</span>
                </>
              ) : metadata.status === 'STITCHING_MASTER' ? (
                <>
                  <Loader2 className="w-3.5 h-3.5 text-[#ff7a45] animate-spin shrink-0" />
                  <span className="text-[#ff7a45] font-medium truncate">Stitching Master MP3 ({metadata.master_mp3_progress || 0}%)</span>
                </>
              ) : (
                <>
                  <Sparkles className="w-3 h-3 text-stone-500 shrink-0" />
                  <span className="truncate">Master stitching queues when all audio is ready</span>
                </>
              )}
            </div>

            {isMasterReady && (
              <a
                href={masterDownloadUrl}
                download
                className="flex items-center gap-1.5 px-3 py-1 bg-emerald-600 hover:bg-emerald-500 text-white text-[11px] font-bold rounded-xl transition-all shrink-0 shadow-md shadow-emerald-600/20"
              >
                <Download className="w-3 h-3" />
                <span>Download</span>
              </a>
            )}
          </div>

          {/* Error Message if present */}
          {metadata.error_message && (
            <div className="p-2.5 rounded-xl bg-red-950/40 border border-red-500/20 text-xs text-red-300">
              {metadata.error_message}
            </div>
          )}
        </div>
      )}
    </div>
  );
};
