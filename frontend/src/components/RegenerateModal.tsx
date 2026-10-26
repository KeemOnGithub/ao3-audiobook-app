import React from 'react';
import { createPortal } from 'react-dom';
import { RefreshCw, Wand2, Volume2, X } from 'lucide-react';
import { ForceRegenerateOption } from '../types/api';

interface RegenerateModalProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: (option: ForceRegenerateOption) => void;
}

export const RegenerateModal: React.FC<RegenerateModalProps> = ({
  isOpen,
  onClose,
  onConfirm,
}) => {
  if (!isOpen) return null;

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4 animate-in fade-in duration-200">
      <div className="relative w-full max-w-lg rounded-2xl border border-slate-800 bg-slate-900 p-6 shadow-2xl">
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-slate-400 hover:text-white transition-colors p-1"
          aria-label="Close"
        >
          <X className="w-5 h-5" />
        </button>

        <div className="flex items-center gap-3 mb-4">
          <div className="p-2.5 rounded-xl bg-indigo-500/10 border border-indigo-500/20 text-indigo-400">
            <RefreshCw className="w-6 h-6 animate-spin-slow" />
          </div>
          <div>
            <h3 className="text-xl font-bold text-white">Regenerate Chapter</h3>
            <p className="text-sm text-slate-400">Choose regeneration scope for this chapter</p>
          </div>
        </div>

        <div className="space-y-3 mt-6">
          {/* Option 1: Audio Only */}
          <button
            onClick={() => {
              onConfirm('AUDIO_ONLY');
              onClose();
            }}
            className="w-full text-left p-4 rounded-xl border border-slate-800 bg-slate-950/60 hover:bg-slate-800/80 hover:border-indigo-500/50 transition-all group flex items-start gap-4"
          >
            <div className="p-2 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 group-hover:scale-105 transition-transform mt-0.5">
              <Volume2 className="w-5 h-5" />
            </div>
            <div>
              <div className="font-semibold text-white group-hover:text-emerald-300 transition-colors">
                Re-synthesize Audio Only
              </div>
              <p className="text-xs text-slate-400 mt-1 leading-relaxed">
                Keeps existing LLM screenplay deconstruction, dialogue attribution, and voice prompts. Re-synthesizes audio across all segments with Qwen3-TTS.
              </p>
            </div>
          </button>

          {/* Option 2: Full Re-script & Audio */}
          <button
            onClick={() => {
              onConfirm('FULL');
              onClose();
            }}
            className="w-full text-left p-4 rounded-xl border border-slate-800 bg-slate-950/60 hover:bg-slate-800/80 hover:border-amber-500/50 transition-all group flex items-start gap-4"
          >
            <div className="p-2 rounded-lg bg-amber-500/10 border border-amber-500/20 text-amber-400 group-hover:scale-105 transition-transform mt-0.5">
              <Wand2 className="w-5 h-5" />
            </div>
            <div>
              <div className="font-semibold text-white group-hover:text-amber-300 transition-colors">
                Full Re-script & Re-synthesize
              </div>
              <p className="text-xs text-slate-400 mt-1 leading-relaxed">
                Starts from scratch with a new script, character profiles, voices, previews, and audio. Previous voice edits are not reused.
              </p>
            </div>
          </button>
        </div>

        <div className="mt-6 flex justify-end">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-slate-400 hover:text-white transition-colors"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
};

