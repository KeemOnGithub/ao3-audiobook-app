import { apiClient, ApiError } from './apiClient';
import { sseManager } from './sseManager';
import { useAppStore } from '../store/useAppStore';
import { ChapterSnapshotResponse, ReviewIdentity } from '../types/api';

export function reviewIdentity(): ReviewIdentity {
  const meta = useAppStore.getState().metadata;
  return {expected_run_generation:meta?.run_generation ?? 0,expected_review_revision:meta?.review_revision ?? 0};
}

export async function runReviewMutation(operation: () => Promise<ChapterSnapshotResponse>): Promise<boolean> {
  const chapterId = useAppStore.getState().metadata?.chapter_id;
  if (!chapterId || useAppStore.getState().reviewBusy) return false;
  useAppStore.getState().setReviewMutation(true);
  try {
    const snapshot = await operation();
    if (useAppStore.getState().metadata?.chapter_id !== chapterId) return false;
    sseManager.reconcileSnapshot(snapshot);
    return true;
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      const drafts = useAppStore.getState().voiceDrafts;
      const snapshot = await apiClient.getChapterSnapshot(chapterId);
      if (useAppStore.getState().metadata?.chapter_id === chapterId) {
        sseManager.reconcileSnapshot(snapshot);
        useAppStore.setState({voiceDrafts:Object.fromEntries(Object.entries(drafts).map(([id,draft]) => [id,{...draft,stale:true}]))});
      }
    }
    useAppStore.getState().setReviewMutation(false,error instanceof Error ? error.message : 'Review operation failed');
    return false;
  } finally {
    useAppStore.setState({reviewBusy:false});
  }
}
