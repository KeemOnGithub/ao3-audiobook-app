import React, { useState } from 'react';
import { Users, Play, Square, ChevronDown, ChevronUp, Mic } from 'lucide-react';
import { useAppStore } from '../store/useAppStore';
import { gaplessAudioEngine } from '../services/GaplessAudioEngine';
import { apiClient } from '../services/apiClient';
import { reviewIdentity, runReviewMutation } from '../services/reviewActions';
import { PipelineDrawer } from './PipelineDrawer';
import { CharacterCorrectionDialog, CorrectionMode } from './CharacterCorrectionDialog';

const FALLBACK_SAMPLE =
  'The evening shadows lengthened across the quiet room as the story began to unfold.';

export const VoiceSidebar: React.FC = () => {
  const characters = useAppStore((s) => s.characters);
  const characterIds = useAppStore((s) => s.characterIds);
  const metadata = useAppStore((s) => s.metadata);
  const drafts = useAppStore((s) => s.voiceDrafts);
  const candidates = useAppStore((s) => s.voiceCandidates);
  const busy = useAppStore((s) => s.reviewBusy);
  const error = useAppStore((s) => s.reviewError);
  const activePreview = useAppStore((s) => s.activePreviewCharacterId);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [correction, setCorrection] = useState<{ mode: CorrectionMode; characterId?: string } | null>(
    null
  );
  const highlighted = useAppStore((s) => s.highlightedCharacterId);
  const editable = metadata?.review_phase === 'AWAITING_APPROVAL';

  return (
    <aside className="w-80 shrink-0 bg-[#16171d]/95 border-l border-white/[0.08] backdrop-blur-xl flex flex-col h-full shadow-2xl z-20">
      {/* Header */}
      <div className="p-4 border-b border-white/[0.08] flex items-center justify-between shrink-0">
        <div className="flex items-center gap-2">
          <Users className="w-4 h-4 text-[#ff7a45]" />
          <h2 className="font-bold text-xs uppercase tracking-wider text-stone-300 font-heading">
            Character Cast ({characterIds.length})
          </h2>
        </div>
        {editable && (
          <button
            className="text-xs font-semibold px-2 py-0.5 rounded-lg bg-[#ff5a1f]/15 text-[#ff7a45] hover:bg-[#ff5a1f]/25 transition-all cursor-pointer"
            onClick={() => setCorrection({ mode: 'add' })}
          >
            Add
          </button>
        )}
      </div>

      {error && <p role="alert" className="p-3 text-xs text-red-300 bg-red-950/40 border-b border-red-800/40">{error}</p>}

      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {!characterIds.length && (
          <div className="text-center py-12 text-stone-500 text-xs">
            <Mic className="w-8 h-8 mx-auto mb-2 text-stone-600" />
            Characters discovered by analysis will appear here.
          </div>
        )}
        {characterIds.map((id) => {
          const char = characters[id];
          const segment = Object.values(useAppStore.getState().segmentsByOrder).find(
            (s) => s.speaker_uuid === id && s.text.trim().length >= 20
          );
          const sample =
            segment?.text.replace(/\s+/g, ' ').trim().slice(0, 250) ||
            char.reference_text ||
            FALLBACK_SAMPLE;
          const draft = drafts[id] || { prompt: char.voice_instruct, sample };
          const expanded = expandedId === id;
          const previews = Object.values(candidates).filter(
            (c) => c.character_uuid === id && c.status !== 'REVOKED'
          );

          return (
            <section
              key={id}
              className="rounded-2xl border border-white/[0.08] bg-white/[0.03] p-3.5 space-y-2.5 hover:border-white/15 transition-all shadow-sm"
            >
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2.5 min-w-0">
                  <span className="w-7 h-7 rounded-xl bg-white/10 text-stone-200 font-bold text-xs flex items-center justify-center shrink-0">
                    {char.is_narrator ? '🎙' : char.canonical_name ? char.canonical_name[0].toUpperCase() : 'C'}
                  </span>
                  <div className="min-w-0">
                    <h3 className="font-bold text-xs text-white truncate">{char.canonical_name}</h3>
                    <span className="text-[10px] text-stone-400 block truncate">
                      {char.is_narrator ? 'Narrator · ' : ''}
                      {char.segment_count} segments ·{' '}
                      {char.preview_url ? `Applied voice v${char.voice_version}` : 'Untested default'}
                    </span>
                  </div>
                </div>

                {char.preview_url && (
                  <button
                    aria-label={`Listen to applied ${char.canonical_name} voice`}
                    onClick={() =>
                      activePreview === id
                        ? gaplessAudioEngine.stopCharacterPreview()
                        : gaplessAudioEngine.playCharacterPreview(id, char.preview_url!)
                    }
                    className="px-2.5 py-1 rounded-xl bg-white/[0.08] hover:bg-white/[0.15] text-[11px] font-semibold text-stone-200 flex items-center gap-1 transition-all shrink-0 cursor-pointer"
                  >
                    {activePreview === id ? (
                      <>
                        <Square className="w-3.5 h-3.5 text-[#ff7a45]" />
                        <span>Stop</span>
                      </>
                    ) : (
                      <>
                        <Play className="w-3.5 h-3.5 text-[#ff7a45] fill-current" />
                        <span>Preview</span>
                      </>
                    )}
                  </button>
                )}
              </div>

              <p className="text-xs text-stone-400 leading-relaxed">{char.voice_instruct}</p>

              {char.voice_status === 'FAILED' && (
                <p role="alert" className="text-xs text-red-300 bg-red-950/40 p-1.5 rounded-lg border border-red-500/20">
                  Voice generation failed
                </p>
              )}
              {char.voice_status === 'GENERATING' && (
                <p className="text-xs text-amber-300 bg-amber-950/40 p-1.5 rounded-lg border border-amber-500/20">
                  Generating approved default voice
                </p>
              )}
              {char.voice_status === 'READY' && !char.preview_url && (
                <p className="text-xs text-stone-400">Preview unavailable</p>
              )}

              {editable && (
                <div className="flex flex-wrap gap-2.5 text-xs text-[#ff7a45] pt-1">
                  <button
                    className="hover:underline cursor-pointer"
                    onClick={() => setCorrection({ mode: 'rename', characterId: id })}
                  >
                    Rename
                  </button>
                  {!char.is_narrator && (
                    <>
                      <button
                        className="hover:underline cursor-pointer"
                        onClick={() => setCorrection({ mode: 'merge', characterId: id })}
                      >
                        Merge
                      </button>
                      <button
                        className="hover:underline cursor-pointer"
                        onClick={() => setCorrection({ mode: 'delete', characterId: id })}
                      >
                        Delete
                      </button>
                    </>
                  )}
                  <button
                    className="hover:underline cursor-pointer"
                    onClick={() =>
                      useAppStore
                        .getState()
                        .setHighlightedCharacter(highlighted === id ? null : id)
                    }
                  >
                    {highlighted === id ? 'Clear Highlight' : 'Highlight Dialogue'}
                  </button>
                </div>
              )}

              {editable && (
                <button
                  className="text-xs text-[#ff7a45] flex items-center gap-1 font-medium hover:underline cursor-pointer pt-1"
                  onClick={() => setExpandedId(expanded ? null : id)}
                >
                  {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                  Review voice
                </button>
              )}

              {editable && expanded && (
                <div className="pt-2.5 border-t border-white/[0.08] space-y-2.5">
                  <label className="block text-xs text-stone-300">
                    Acoustic prompt
                    <textarea
                      aria-label={`Voice prompt for ${char.canonical_name}`}
                      rows={3}
                      minLength={10}
                      maxLength={600}
                      value={draft.prompt}
                      onChange={(e) =>
                        useAppStore.getState().setVoiceDraft(id, { ...draft, prompt: e.target.value })
                      }
                      className="w-full bg-[#101114] border border-white/10 rounded-xl p-2.5 mt-1 text-xs text-stone-200 focus:outline-none focus:border-[#ff5a1f]"
                    />
                  </label>
                  <label className="block text-xs text-stone-300">
                    Sample spoken in preview (up to 15 seconds)
                    <textarea
                      aria-label={`Preview sample for ${char.canonical_name}`}
                      rows={3}
                      minLength={20}
                      maxLength={250}
                      value={draft.sample}
                      onChange={(e) =>
                        useAppStore.getState().setVoiceDraft(id, { ...draft, sample: e.target.value })
                      }
                      className="w-full bg-[#101114] border border-white/10 rounded-xl p-2.5 mt-1 text-xs text-stone-200 focus:outline-none focus:border-[#ff5a1f]"
                    />
                  </label>
                  {drafts[id] && (
                    <p className="text-xs text-amber-300">
                      {drafts[id].stale
                        ? 'Review changed. Compare this preserved draft before retrying.'
                        : 'Unapplied draft'}
                    </p>
                  )}
                  <button
                    disabled={
                      busy || draft.prompt.trim().length < 10 || draft.sample.trim().length < 20
                    }
                    className="px-3 py-1.5 bg-[#ff5a1f] hover:bg-[#ff7038] disabled:bg-stone-800 disabled:text-stone-500 rounded-xl text-xs font-semibold text-white transition-all cursor-pointer disabled:cursor-not-allowed shadow-md shadow-[#ff5a1f]/20"
                    onClick={() =>
                      runReviewMutation(
                        async () =>
                          (
                            await apiClient.createVoicePreview(
                              metadata!.chapter_id,
                              id,
                              reviewIdentity(),
                              draft.prompt,
                              draft.sample
                            )
                          ).snapshot
                      )
                    }
                  >
                    Generate Preview
                  </button>

                  {previews.map((candidate) => (
                    <div
                      key={candidate.candidate_uuid}
                      className="border border-white/10 bg-black/20 rounded-xl p-2.5 text-xs space-y-2"
                    >
                      <p className="font-semibold text-stone-300">
                        {candidate.status === 'READY'
                          ? 'Ready to audition, not applied'
                          : candidate.status}
                      </p>
                      <p className="text-stone-400">{candidate.voice_instruct}</p>
                      <p className="text-stone-400">Sample: {candidate.reference_text}</p>
                      {candidate.error_message && (
                        <p role="alert" className="text-red-300">
                          {candidate.error_message}
                        </p>
                      )}
                      {candidate.status === 'READY' && (
                        <div className="flex gap-3 pt-1">
                          <button
                            className="px-2.5 py-1 rounded-lg bg-white/[0.08] hover:bg-white/[0.14] text-xs font-semibold text-stone-200"
                            onClick={() =>
                              gaplessAudioEngine.playCharacterPreview(
                                candidate.candidate_uuid,
                                candidate.preview_url!
                              )
                            }
                          >
                            Listen
                          </button>
                          <button
                            disabled={busy}
                            className="px-2.5 py-1 rounded-lg bg-[#ff5a1f] hover:bg-[#ff7038] text-xs font-semibold text-white disabled:opacity-40"
                            onClick={async () => {
                              if (
                                await runReviewMutation(() =>
                                  apiClient.reviewMutation(
                                    metadata!.chapter_id,
                                    `voice-previews/${candidate.candidate_uuid}/apply`,
                                    reviewIdentity()
                                  )
                                )
                              ) {
                                useAppStore.getState().clearVoiceDraft(id);
                              }
                            }}
                          >
                            Apply This Voice
                          </button>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </section>
          );
        })}
      </div>

      {metadata && <PipelineDrawer />}
      {correction && (
        <CharacterCorrectionDialog {...correction} onClose={() => setCorrection(null)} />
      )}
    </aside>
  );
};
