import { useAppStore } from '../store/useAppStore';
import { gaplessAudioEngine } from './GaplessAudioEngine';
import { sseManager } from './sseManager';

let selectionGeneration = 0;

export function beginChapterSelection() {
  const generation = ++selectionGeneration;
  gaplessAudioEngine.reset();
  sseManager.disconnect();
  useAppStore.getState().resetChapterState();
  return () => generation === selectionGeneration;
}

export async function openSavedChapter(chapterUuid: string): Promise<boolean> {
  const isCurrent = beginChapterSelection();
  try {
    await sseManager.connect(chapterUuid, { saved: true });
    if (!isCurrent()) return false;
    const metadata = useAppStore.getState().metadata;
    if (!metadata || metadata.chapter_id !== chapterUuid) {
      throw new Error('Saved chapter could not be loaded.');
    }
    localStorage.setItem('ao3_audiobook_last_chapter_uuid', chapterUuid);
    localStorage.setItem('ao3_audiobook_last_url', metadata.source_url);
    return true;
  } catch (error) {
    if (!isCurrent()) return false;
    sseManager.disconnect();
    throw error;
  }
}
