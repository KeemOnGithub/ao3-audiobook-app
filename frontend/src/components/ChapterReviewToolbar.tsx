import { useAppStore } from '../store/useAppStore';
import { apiClient } from '../services/apiClient';
import { reviewIdentity, runReviewMutation } from '../services/reviewActions';
import { gaplessAudioEngine } from '../services/GaplessAudioEngine';
import { sseManager } from '../services/sseManager';

export function ChapterReviewToolbar() {
  const meta = useAppStore((s) => s.metadata);
  const busy = useAppStore((s) => s.reviewBusy);
  const error = useAppStore((s) => s.reviewError);
  const drafts = useAppStore((s) => s.voiceDrafts);
  const candidates = useAppStore((s) => s.voiceCandidates);
  const cast = useAppStore((s) => s.characters);
  if (!meta) return null;
  const review = meta.review_phase === 'AWAITING_APPROVAL';
  const canReopen =
    Boolean(meta.analysis_completed_at) &&
    ['SYNTHESIZING', 'MASTERING', 'COMPLETED', 'FAILED'].includes(meta.review_phase || '');

  const approve = async () => {
    if (
      (Object.keys(drafts).length ||
        Object.values(candidates).some((c) => c.status !== 'REVOKED' && c.status !== 'FAILED')) &&
      !window.confirm(
        'There are unapplied drafts or candidates. Cancel to apply a READY candidate first, or continue to discard drafts and generate using currently selected/default voices.'
      )
    )
      return;
    gaplessAudioEngine.reset();
    if (
      await runReviewMutation(() =>
        apiClient.reviewMutation(meta.chapter_id, 'approve', reviewIdentity())
      )
    ) {
      useAppStore.getState().clearVoiceDraft();
      await sseManager.connect(meta.chapter_id, { saved: true });
      if (
        useAppStore.getState().autoplayEnabled &&
        useAppStore.getState().metadata?.chapter_id === meta.chapter_id
      ) {
        gaplessAudioEngine.setAutoplayIntent();
        void gaplessAudioEngine.play();
      }
    }
  };

  return (
    <section
      aria-label="Chapter review"
      className="shrink-0 border-b border-white/[0.08] bg-[#16171d]/90 backdrop-blur-md p-3.5 md:px-6 space-y-2"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-bold text-sm text-white font-heading">
            {review
              ? 'Review Complete Chapter'
              : meta.review_phase === 'ANALYZING'
              ? 'Analyzing Complete Chapter'
              : meta.review_phase || 'Preparing Chapter'}
          </h2>
          <p className="text-xs text-stone-400">
            {review
              ? `${meta.total_segments} segments, ${Object.keys(cast).length} cast members. Correct speakers and optionally audition voices before approval.`
              : meta.review_phase === 'ANALYZING'
              ? 'No voice or narration is generated until you explicitly approve.'
              : 'Return to review to change speakers or voices.'}
          </p>
        </div>
        {review && (
          <button
            disabled={busy || !meta.can_approve}
            className="px-4 py-2 rounded-xl bg-[#ff5a1f] hover:bg-[#ff7038] disabled:bg-stone-800 disabled:text-stone-500 text-white text-xs font-bold transition-all shadow-md shadow-[#ff5a1f]/20 cursor-pointer disabled:cursor-not-allowed"
            onClick={approve}
          >
            {busy ? 'Saving...' : 'Approve & Generate Chapter'}
          </button>
        )}
        {canReopen && (
          <button
            disabled={busy}
            className="px-4 py-2 rounded-xl border border-amber-600/60 hover:bg-amber-950/30 text-amber-300 text-xs font-semibold transition-all cursor-pointer"
            onClick={async () => {
              if (
                !window.confirm(
                  'Return to review? All previous narration and master audio become unavailable. Reapproval regenerates every segment.'
                )
              )
                return;
              gaplessAudioEngine.reset();
              if (
                await runReviewMutation(() =>
                  apiClient.reviewMutation(meta.chapter_id, 'reopen', reviewIdentity())
                )
              )
                await sseManager.connect(meta.chapter_id, { saved: true });
            }}
          >
            Return To Review
          </button>
        )}
      </div>
      {review && (
        <p className="text-xs text-stone-400">
          Untested default voices are allowed. Listening does not apply a candidate; use Apply This Voice to select it.
        </p>
      )}
      {review &&
        meta.approval_blockers?.map((blocker) => (
          <p key={blocker.code} className="text-xs text-amber-300">
            {blocker.message}
          </p>
        ))}
      {error && (
        <p role="alert" className="text-xs text-red-300 bg-red-950/40 p-2 rounded-xl border border-red-500/20">
          {error}
        </p>
      )}
    </section>
  );
}
