import { apiClient } from './apiClient';
import { useAppStore } from '../store/useAppStore';
import { PlaybackSpeed, ScriptSegment } from '../types/domain';

const SILENT_WAV =
  'data:audio/wav;base64,UklGRigAAABXQVZFZm10IBIAAAABAAEARKwAAIhYAQACABAAAABkYXRhAgAAAAEA';

export type PlayIntent = 'NONE' | 'USER_PLAY' | 'AUTOPLAY';

export class GaplessAudioEngine {
  private activeNode: HTMLAudioElement;
  private prefetchNode: HTMLAudioElement;
  private previewNode: HTMLAudioElement;

  private isUnlocked = false;
  private prefetchedOrder: number | null = null;
  private pauseTimer: ReturnType<typeof setTimeout> | null = null;
  private seekDebounceTimer: ReturnType<typeof setTimeout> | null = null;

  private playIntent: PlayIntent = 'NONE';
  private operationGeneration = 0;

  constructor() {
    this.activeNode = new Audio();
    this.prefetchNode = new Audio();
    this.previewNode = new Audio();

    this.setupNode(this.activeNode);
    this.setupNode(this.prefetchNode);
    this.setupPreviewNode(this.previewNode);

    this.bindEvents();
    useAppStore.subscribe(state => `${state.metadata?.chapter_id}:${state.metadata?.run_generation}:${state.metadata?.narration_authorized}`,
      () => { this.reset(); useAppStore.getState().setPlaybackStatus('IDLE'); });
  }

  private authorized(): boolean {
    const meta=useAppStore.getState().metadata;
    return meta?.narration_authorized === true && ['SYNTHESIZING','MASTERING','COMPLETED'].includes(meta.review_phase || '');
  }

  getPlayIntent(): PlayIntent {
    return this.playIntent;
  }

  setAutoplayIntent(): void {
    if (!this.authorized()) return;
    this.playIntent = 'AUTOPLAY';
  }

  private setupNode(node: HTMLAudioElement) {
    node.preload = 'auto';
    (node as any).preservesPitch = true;
  }

  private setupPreviewNode(node: HTMLAudioElement) {
    node.preload = 'auto';
    node.onended = () => {
      useAppStore.getState().setActivePreviewCharacterId(null);
    };
    node.onerror = () => {
      useAppStore.getState().setActivePreviewCharacterId(null);
    };
  }

  private bindEvents() {
    // ontimeupdate on activeNode: updates elapsed ms (~4Hz) and checks prefetch Trigger 2
    this.activeNode.ontimeupdate = () => {
      const elapsedMs = Math.round(this.activeNode.currentTime * 1000);
      const store = useAppStore.getState();
      store.setCurrentSegmentElapsedMs(elapsedMs);

      const activeOrder = store.activeSegmentOrder;
      const currentSeg = store.segmentsByOrder[activeOrder];
      if (!currentSeg) return;

      const durMs = currentSeg.duration_ms || (this.activeNode.duration ? this.activeNode.duration * 1000 : 0);
      if (durMs > 0) {
        const remainingMs = durMs - elapsedMs;
        const progressRatio = elapsedMs / durMs;

        // Trigger 2: At >= 60% duration OR <= 1.5s remaining
        if ((progressRatio >= 0.6 || remainingMs <= 1500) && this.prefetchedOrder !== activeOrder + 1) {
          this.checkAndPrefetchNext(activeOrder);
        }
      }
    };

    // activeNode ended: transition through inter-segment pause
    this.activeNode.onended = () => {
      this.handleActiveSegmentEnded();
    };

    this.activeNode.onerror = (e) => {
      console.warn('activeNode error:', e);
      this.playIntent = 'NONE';
      this.operationGeneration++;
      useAppStore.getState().setPlaybackStatus('PAUSED');
    };
  }

  /**
   * Browser Autoplay Unlock Rule (Mandatory)
   * Must be called synchronously inside user click event handler.
   */
  async unlockAudioContext(): Promise<void> {
    if (this.isUnlocked) return;

    try {
      this.activeNode.src = SILENT_WAV;
      this.prefetchNode.src = SILENT_WAV;
      await Promise.all([
        this.activeNode.play().catch(() => {}),
        this.prefetchNode.play().catch(() => {}),
      ]);
      this.activeNode.pause();
      this.prefetchNode.pause();
      this.activeNode.currentTime = 0;
      this.prefetchNode.currentTime = 0;
      this.activeNode.src = '';
      this.prefetchNode.src = '';
      this.isUnlocked = true;
    } catch (err) {
      console.warn('AudioContext unlock failed:', err);
    }
  }

  private resolveSegmentAudioUrl(seg: ScriptSegment): string {
    if (!seg.audio_url) return '';
    const separator = seg.audio_url.includes('?') ? '&' : '?';
    return `${seg.audio_url}${separator}v=${seg.voice_version}`;
  }

  /**
   * Evaluates Trigger 1 / Trigger 2 prefetch for Segment N + 1
   */
  private checkAndPrefetchNext(currentOrder: number) {
    if (!this.authorized()) return;
    const nextOrder = currentOrder + 1;
    if (this.prefetchedOrder === nextOrder) return;

    const store = useAppStore.getState();
    const nextSeg = store.segmentsByOrder[nextOrder];

    if (nextSeg && nextSeg.audio_status === 'READY' && nextSeg.audio_url) {
      const url = this.resolveSegmentAudioUrl(nextSeg);
      this.prefetchNode.src = url;
      this.prefetchNode.load();
      this.prefetchedOrder = nextOrder;
    }
  }

  /**
   * Handles segment playback completion and computes narrative pauses
   */
  private handleActiveSegmentEnded() {
    if (!this.authorized()) { this.reset(); return; }
    this.clearPauseTimer();

    if (this.playIntent === 'NONE') {
      useAppStore.getState().setPlaybackStatus('PAUSED');
      return;
    }

    const store = useAppStore.getState();
    const currentOrder = store.activeSegmentOrder;
    const currentSeg = store.segmentsByOrder[currentOrder];
    const speed = store.playbackSpeed;

    const basePauseMs = currentSeg?.is_paragraph_end ? 650 : 250;
    const effectivePauseMs = Math.round(basePauseMs / speed);

    store.setPlaybackStatus('INTER_SEGMENT_PAUSE');

    const capturedOp = this.operationGeneration;
    this.pauseTimer = setTimeout(() => {
      if (this.operationGeneration !== capturedOp || this.playIntent === 'NONE') {
        return;
      }
      this.advanceToNextSegment(currentOrder + 1);
    }, effectivePauseMs);
  }

  private advanceToNextSegment(nextOrder: number) {
    if (!this.authorized()) { this.reset(); return; }
    if (this.playIntent === 'NONE') return;

    const store = useAppStore.getState();
    const nextSeg = store.segmentsByOrder[nextOrder];
    const meta = store.metadata;

    // Case A: Next segment does not exist and chapter is COMPLETED
    if (!nextSeg && meta?.status === 'COMPLETED') {
      this.playIntent = 'NONE';
      store.setPlaybackStatus('COMPLETED');
      return;
    }

    // Case B: Next segment is in ERROR
    if (nextSeg && nextSeg.audio_status === 'ERROR') {
      this.playIntent = 'NONE';
      store.setActiveSegmentOrder(nextOrder);
      store.setCurrentSegmentElapsedMs(0);
      store.setPlaybackStatus('PAUSED');
      return;
    }

    // Case C: Next segment does not exist yet or audio not READY
    if (!nextSeg || nextSeg.audio_status !== 'READY') {
      store.setActiveSegmentOrder(nextOrder);
      store.setCurrentSegmentElapsedMs(0);
      store.setPlaybackStatus('WAITING_FOR_TTS');
      return;
    }

    // Case D: Next segment is READY -> Swap nodes
    this.swapAndPlay(nextOrder);
  }

  private async swapAndPlay(order: number): Promise<void> {
    if (!this.authorized()) return;
    const store = useAppStore.getState();
    if (this.playIntent === 'NONE') {
      if (store.playbackStatus === 'WAITING_FOR_TTS') {
        this.playIntent = store.autoplayEnabled ? 'AUTOPLAY' : 'USER_PLAY';
      } else {
        return;
      }
    }

    const seg = store.segmentsByOrder[order];
    if (!seg || !seg.audio_url) return;

    if (seg.audio_status === 'ERROR') {
      this.playIntent = 'NONE';
      store.setPlaybackStatus('PAUSED');
      return;
    }

    const speed = store.playbackSpeed;

    if (this.prefetchedOrder === order && this.prefetchNode.src) {
      // Node swap
      const temp = this.activeNode;
      this.activeNode = this.prefetchNode;
      this.prefetchNode = temp;
      this.prefetchedOrder = null;

      // Re-bind listeners to newly active node
      this.setupNode(this.activeNode);
      this.bindEvents();
    } else {
      // Load directly
      this.activeNode.src = this.resolveSegmentAudioUrl(seg);
    }

    this.activeNode.playbackRate = speed;
    store.setActiveSegmentOrder(order);
    store.setCurrentSegmentElapsedMs(0);

    const currentOp = ++this.operationGeneration;
    const durMs = seg.duration_ms || 0;

    try {
      await this.activeNode.play();
      if (this.operationGeneration !== currentOp || (this.playIntent as PlayIntent) === 'NONE') {
        this.activeNode.pause();
        return;
      }
      store.setPlaybackStatus('PLAYING');

      // Trigger 1 (Immediate): If N has duration < 2000ms, immediately prefetch N+1
      if (durMs > 0 && durMs < 2000) {
        this.checkAndPrefetchNext(order);
      }
    } catch (err) {
      console.warn('Playback play failed:', err);
      if (this.operationGeneration === currentOp) {
        this.playIntent = 'NONE';
        store.setPlaybackStatus('PAUSED');
      }
    }
  }

  /**
   * Called by SSEManager when SSE event `segment_audio_ready` arrives
   */
  async onSegmentReady(readyOrder: number): Promise<void> {
    if (!this.authorized()) return;
    const store = useAppStore.getState();
    const activeOrder = store.activeSegmentOrder;
    const status = store.playbackStatus;

    // Reactive recovery & auto-play: only if playIntent remains active or in waiting state
    if (activeOrder === readyOrder) {
      if (
        (this.playIntent !== 'NONE' && status === 'WAITING_FOR_TTS') ||
        status === 'WAITING_FOR_TTS' ||
        (status === 'IDLE' && store.autoplayEnabled && this.playIntent !== 'NONE')
      ) {
        if (this.playIntent === 'NONE') {
          this.playIntent = store.autoplayEnabled ? 'AUTOPLAY' : 'USER_PLAY';
        }
        await this.swapAndPlay(readyOrder);
        return;
      }
    }

    // Reactive prefetch: if N is currently playing and N+1 just became ready
    if (status === 'PLAYING' && readyOrder === activeOrder + 1 && this.prefetchedOrder !== readyOrder) {
      this.checkAndPrefetchNext(activeOrder);
    }
  }

  /**
   * Public playback transport controls
   */
  async play(): Promise<void> {
    if (!this.authorized()) return;
    await this.unlockAudioContext();
    if (!this.authorized()) return;
    this.playIntent = 'USER_PLAY';
    const currentOp = ++this.operationGeneration;

    const store = useAppStore.getState();
    const currentOrder = store.activeSegmentOrder;
    const currentSeg = store.segmentsByOrder[currentOrder];

    if (!currentSeg || currentSeg.audio_status !== 'READY') {
      if (currentSeg && currentSeg.audio_status === 'ERROR') {
        this.playIntent = 'NONE';
        store.setPlaybackStatus('PAUSED');
        return;
      }
      store.setPlaybackStatus('WAITING_FOR_TTS');
      return;
    }

    if (!this.activeNode.src || !this.activeNode.src.includes(currentSeg.segment_uuid)) {
      this.activeNode.src = this.resolveSegmentAudioUrl(currentSeg);
    }

    this.activeNode.playbackRate = store.playbackSpeed;

    try {
      await this.activeNode.play();
      if (this.operationGeneration !== currentOp || (this.playIntent as PlayIntent) === 'NONE') {
        this.activeNode.pause();
        return;
      }
      store.setPlaybackStatus('PLAYING');
      this.checkAndPrefetchNext(currentOrder);
    } catch (err) {
      console.warn('Play call rejected:', err);
      if (this.operationGeneration === currentOp) {
        this.playIntent = 'NONE';
        store.setPlaybackStatus('PAUSED');
      }
    }
  }

  pause(): void {
    this.playIntent = 'NONE';
    this.operationGeneration++;
    this.clearPauseTimer();
    this.activeNode.pause();
    this.prefetchNode.pause();
    useAppStore.getState().setPlaybackStatus('PAUSED');
  }

  reset(): void {
    this.playIntent = 'NONE';
    this.operationGeneration++;
    this.clearPauseTimer();
    if (this.seekDebounceTimer) {
      clearTimeout(this.seekDebounceTimer);
      this.seekDebounceTimer = null;
    }
    this.activeNode.pause();
    this.activeNode.currentTime = 0;
    this.activeNode.src = '';
    this.prefetchNode.pause();
    this.prefetchNode.currentTime = 0;
    this.prefetchNode.src = '';
    this.previewNode.pause();
    this.previewNode.currentTime = 0;
    this.previewNode.src = '';
    this.prefetchedOrder = null;
    useAppStore.getState().setActivePreviewCharacterId(null);
  }

  setSpeed(speed: PlaybackSpeed): void {
    useAppStore.getState().setPlaybackSpeed(speed);
    this.activeNode.playbackRate = speed;
    this.prefetchNode.playbackRate = speed;
  }

  /**
   * Click-to-seek to an arbitrary segment_order
   */
  seekToSegment(targetOrder: number): void {
    if (!this.authorized()) return;
    this.unlockAudioContext().catch(() => {});
    this.clearPauseTimer();
    this.activeNode.pause();
    this.prefetchNode.pause();
    this.prefetchedOrder = null;
    this.prefetchNode.src = '';

    const store = useAppStore.getState();
    store.setActiveSegmentOrder(targetOrder);
    store.setCurrentSegmentElapsedMs(0);

    const targetSeg = store.segmentsByOrder[targetOrder];
    if (targetSeg && targetSeg.audio_status === 'READY') {
      this.playIntent = 'USER_PLAY';
      this.swapAndPlay(targetOrder);
    } else if (targetSeg && targetSeg.audio_status === 'ERROR') {
      this.playIntent = 'NONE';
      store.setPlaybackStatus('PAUSED');
    } else {
      this.playIntent = 'USER_PLAY';
      store.setPlaybackStatus('WAITING_FOR_TTS');

      // Debounce seek priority notification to backend
      if (this.seekDebounceTimer) {
        clearTimeout(this.seekDebounceTimer);
      }
      this.seekDebounceTimer = setTimeout(() => {
        const chapterId = store.metadata?.chapter_id;
        if (chapterId && this.authorized() && useAppStore.getState().metadata?.chapter_id===chapterId) {
          apiClient.seekPlayback(chapterId, targetOrder).catch(() => {});
        }
      }, 150);
    }
  }

  /**
   * Cross-segment skip (±10s)
   */
  skipBySeconds(deltaSeconds: number): void {
    if (!this.authorized()) return;
    const store = useAppStore.getState();
    const currentOrder = store.activeSegmentOrder;
    const currentSeg = store.segmentsByOrder[currentOrder];
    const orders = store.segmentOrders;
    if (orders.length === 0) return;

    const deltaMs = deltaSeconds * 1000;
    const currentElapsedMs = store.currentSegmentElapsedMs;

    if (deltaSeconds < 0) {
      // Skipping backwards
      if (currentElapsedMs + deltaMs >= 0) {
        const newSec = (currentElapsedMs + deltaMs) / 1000;
        this.activeNode.currentTime = Math.max(0, newSec);
        store.setCurrentSegmentElapsedMs(Math.round(this.activeNode.currentTime * 1000));
        return;
      }

      // Walk backwards across segments
      let deficitMs = -(currentElapsedMs + deltaMs);
      let targetOrder = currentOrder;

      while (targetOrder > 0) {
        targetOrder--;
        const prevSeg = store.segmentsByOrder[targetOrder];
        const segDur = prevSeg?.duration_ms || 2000;
        const pauseDur = prevSeg?.is_paragraph_end ? 650 : 250;
        const totalSegMs = segDur + pauseDur;

        if (deficitMs <= totalSegMs) {
          const offsetMs = Math.max(0, totalSegMs - deficitMs);
          this.seekToSegment(targetOrder);
          this.activeNode.currentTime = Math.min(offsetMs, segDur) / 1000;
          return;
        }
        deficitMs -= totalSegMs;
      }

      // Clamped to chapter start
      this.seekToSegment(0);
      this.activeNode.currentTime = 0;
    } else {
      // Skipping forwards
      const curDur = currentSeg?.duration_ms || (this.activeNode.duration ? this.activeNode.duration * 1000 : 2000);
      const remainingInCur = curDur - currentElapsedMs;

      if (deltaMs < remainingInCur) {
        this.activeNode.currentTime = (currentElapsedMs + deltaMs) / 1000;
        store.setCurrentSegmentElapsedMs(Math.round(this.activeNode.currentTime * 1000));
        return;
      }

      // Walk forwards across segments
      let surplusMs = deltaMs - remainingInCur;
      let targetOrder = currentOrder;

      while (targetOrder < orders.length - 1) {
        targetOrder++;
        const nextSeg = store.segmentsByOrder[targetOrder];
        if (!nextSeg || nextSeg.audio_status !== 'READY' || nextSeg.duration_ms === null) {
          this.seekToSegment(targetOrder);
          return;
        }

        const pauseDur = nextSeg.is_paragraph_end ? 650 : 250;
        const totalSegMs = nextSeg.duration_ms + pauseDur;

        if (surplusMs < totalSegMs) {
          this.seekToSegment(targetOrder);
          this.activeNode.currentTime = Math.min(surplusMs, nextSeg.duration_ms) / 1000;
          return;
        }
        surplusMs -= totalSegMs;
      }

      this.seekToSegment(orders[orders.length - 1]);
    }
  }

  /**
   * Hot-swap voice invalidation: discard prefetched audio if it belonged to modified character
   */
  onCharacterVoiceInvalidated(characterUuid: string): void {
    const store = useAppStore.getState();
    if (this.prefetchedOrder !== null) {
      const prefetchedSeg = store.segmentsByOrder[this.prefetchedOrder];
      if (prefetchedSeg && prefetchedSeg.speaker_uuid === characterUuid) {
        this.prefetchNode.src = '';
        this.prefetchedOrder = null;
      }
    }
  }

  /**
   * Sidebar 5s Character Sample Preview
   */
  async playCharacterPreview(characterUuid: string, previewUrl: string): Promise<void> {
    const store = useAppStore.getState();
    this.pause();

    store.setActivePreviewCharacterId(characterUuid);
    this.previewNode.src = previewUrl;
    await this.previewNode.play().catch((err) => {
      console.warn('Preview playback failed:', err);
      store.setActivePreviewCharacterId(null);
    });
  }

  stopCharacterPreview(): void {
    this.previewNode.pause();
    this.previewNode.currentTime = 0;
    this.previewNode.src = '';
    useAppStore.getState().setActivePreviewCharacterId(null);
  }

  private clearPauseTimer() {
    if (this.pauseTimer) {
      clearTimeout(this.pauseTimer);
      this.pauseTimer = null;
    }
  }
}

export const gaplessAudioEngine = new GaplessAudioEngine();
