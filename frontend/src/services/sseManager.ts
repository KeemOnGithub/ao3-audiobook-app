import { apiClient } from './apiClient';
import { useAppStore } from '../store/useAppStore';
import {
  BaseSSEEventEnvelope,
  ChapterCompletedEventPayload,
  ChapterFailedEventPayload,
  ChapterMasteringEventPayload,
  ChapterSnapshotResponse,
  ChapterStatusEventPayload,
  CharacterRegisteredEventPayload,
  CharacterVoiceFailedEventPayload,
  CharacterVoiceReadyEventPayload,
  ChunkScriptedEventPayload,
  MasterMp3ProgressEventPayload,
  SegmentAudioReadyEventPayload,
  SegmentFailedEventPayload,
  SnapshotRequiredEventPayload,
} from '../types/api';
import {
  Character,
  CharacterVoiceStatus,
  NormalizedChapterSnapshot,
  PipelineStage,
  ScriptSegment,
  SegmentAudioStatus,
} from '../types/domain';
import { gaplessAudioEngine } from './GaplessAudioEngine';

function mapWireVoiceStatus(status?: string): CharacterVoiceStatus {
  switch (status) {
    case 'PENDING':
      return 'PENDING';
    case 'GENERATING':
    case 'DESIGNING_REFERENCE':
    case 'EXTRACTING_EMBEDDING':
    case 'UPDATING':
      return 'GENERATING';
    case 'READY':
      return 'READY';
    case 'FAILED':
    case 'ERROR':
      return 'FAILED';
    default:
      return 'READY';
  }
}

function mapWireSegmentStatus(status: string): SegmentAudioStatus {
  switch (status) {
    case 'READY':
      return 'READY';
    case 'QUEUED':
      return 'PENDING_TTS';
    case 'SYNTHESIZING':
      return 'SYNTHESIZING';
    case 'FAILED':
    case 'ERROR':
      return 'ERROR';
    case 'STALE_VOICE':
      return 'STALE_VOICE';
    default:
      return 'PENDING_TTS';
  }
}

class SSEManager {
  private eventSource: EventSource | null = null;
  private currentChapterId: string | null = null;
  private reconnectAttempts = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private isExplicitlyClosed = false;

  private connectionGeneration = 0;
  private lastAppliedRevision = -1;
  private currentRunGeneration = 0;

  async connect(chapterId: string, options: { saved?: boolean } = {}) {
    const nextGen = ++this.connectionGeneration;

    this.disconnect(false);
    this.currentChapterId = chapterId;
    this.isExplicitlyClosed = false;
    this.reconnectAttempts = 0;
    this.lastAppliedRevision = -1;
    this.currentRunGeneration = 0;

    await this.fetchAndReconcileSnapshot(chapterId, nextGen, Boolean(options.saved));
    if (this.connectionGeneration !== nextGen || this.isExplicitlyClosed) return;

    this.initEventSource(nextGen);
  }

  async fetchAndReconcileSnapshot(chapterId: string, gen = this.connectionGeneration, throwOnError = false) {
    if (gen !== this.connectionGeneration || this.isExplicitlyClosed) return;

    try {
      let applied=false;
      for (let attempt=0;attempt<3;attempt++) {
        const snapshot = await apiClient.getChapterSnapshot(chapterId);
        if (gen !== this.connectionGeneration || this.isExplicitlyClosed) return;
        if (this.reconcileSnapshot(snapshot)) {applied=true;break;}
      }
      if (!applied) {
        this.reconnectTimer=setTimeout(()=>{void this.fetchAndReconcileSnapshot(chapterId,gen);},1000);
        return;
      }

      // Auto-play recovery if active segment is ready AND intent exists or autoplay enabled
      const store = useAppStore.getState();
      const activeOrder = store.activeSegmentOrder;
      const currentSeg = store.segmentsByOrder[activeOrder];

      if (
        store.metadata?.narration_authorized===true && gaplessAudioEngine.getPlayIntent() !== 'NONE' &&
        (store.playbackStatus === 'WAITING_FOR_TTS' || store.playbackStatus === 'IDLE') &&
        currentSeg &&
        currentSeg.audio_status === 'READY'
      ) {
        gaplessAudioEngine.onSegmentReady(activeOrder);
      }
    } catch (err) {
      if (throwOnError) throw err;
      console.warn('Failed to fetch snapshot:', err);
    }
  }

  private isEventStale(event: BaseSSEEventEnvelope, gen: number): boolean {
    if (this.isExplicitlyClosed || gen !== this.connectionGeneration) return true;
    if (this.currentChapterId && event.chapter_uuid !== this.currentChapterId) return true;
    if (event.run_generation < this.currentRunGeneration) return true;
    if (event.run_generation === this.currentRunGeneration && event.state_revision <= this.lastAppliedRevision) {
      return true;
    }
    return false;
  }

  private advanceRevision(event: BaseSSEEventEnvelope) {
    if (event.run_generation > this.currentRunGeneration) {
      this.currentRunGeneration = event.run_generation;
    }
    this.lastAppliedRevision = Math.max(this.lastAppliedRevision, event.state_revision);
  }

  private initEventSource(gen: number) {
    if (!this.currentChapterId || this.isExplicitlyClosed || gen !== this.connectionGeneration) return;

    const url = `/api/v1/chapters/${this.currentChapterId}/events`;
    this.eventSource = new EventSource(url);

    this.eventSource.addEventListener('chapter_review_changed',(e: MessageEvent) => {
      const event: BaseSSEEventEnvelope = JSON.parse(e.data);
      if (this.isEventStale(event,gen)) return;
      this.advanceRevision(event);
      this.fetchAndReconcileSnapshot(event.chapter_uuid,gen);
    });

    this.eventSource.onopen = () => {
      if (gen !== this.connectionGeneration) {
        this.eventSource?.close();
        return;
      }
      this.reconnectAttempts = 0;
    };

    // 1. chapter_status
    this.eventSource.addEventListener('chapter_status', (e: MessageEvent) => {
      try {
        const data: ChapterStatusEventPayload = JSON.parse(e.data);
        if (this.isEventStale(data, gen)) return;
        this.advanceRevision(data);

        const store = useAppStore.getState();
        const isFailed = data.status === 'FAILED';
        const isCompleted = data.status === 'COMPLETED';
        const isMastering = data.status === 'MASTERING';

        store.setMetadata({
          status: data.status,
          total_chunks: data.total_chunks ?? store.metadata?.total_chunks ?? 0,
          scripted_chunks: data.scripted_chunks ?? store.metadata?.scripted_chunks ?? 0,
          total_segments: data.total_segments ?? store.metadata?.total_segments ?? 0,
          ready_segments: data.ready_segments ?? store.metadata?.ready_segments ?? 0,
          error_message: data.error_message || null,
          run_generation: data.run_generation,
          state_revision: data.state_revision,
        });

        if (isFailed) {
          store.setPipelineFailure(data.error_message || 'Pipeline failed');
          this.disconnect(true);
          return;
        }

        const totalChunks = data.total_chunks ?? store.metadata?.total_chunks ?? store.pipeline.totalChunks;
        const scriptedChunks = data.scripted_chunks ?? store.metadata?.scripted_chunks ?? 0;

        let stage: PipelineStage = store.pipeline.stage;
        if (isCompleted) {
          stage = 'COMPLETED';
        } else if (isMastering) {
          stage = 'MASTERING';
        } else if (store.metadata?.review_phase==='AWAITING_APPROVAL') {
          stage = 'AWAITING_APPROVAL';
        } else if (store.metadata?.review_phase==='SYNTHESIZING') {
          stage = 'SYNTHESIZING_AUDIO';
        } else if (totalChunks > 0) {
          stage = 'ANALYZING_SCRIPT';
        }

        store.setPipelineProgress({
          stage,
          totalChunks,
          currentChunk: Math.min(scriptedChunks + 1, Math.max(totalChunks, 1)),
          totalSegments: data.total_segments ?? store.metadata?.total_segments ?? store.pipeline.totalSegments,
          readySegments: data.ready_segments ?? store.metadata?.ready_segments ?? store.pipeline.readySegments,
        });
      } catch (err) {
        console.error('Failed to parse chapter_status SSE:', err);
      }
    });

    // 2. chapter_failed
    this.eventSource.addEventListener('chapter_failed', (e: MessageEvent) => {
      try {
        const data: ChapterFailedEventPayload = JSON.parse(e.data);
        if (this.isEventStale(data, gen)) return;
        this.advanceRevision(data);

        useAppStore.getState().setPipelineFailure(data.error_message || 'Chapter processing failed', data.error_code || undefined);
        this.disconnect(true);
      } catch (err) {
        console.error('Failed to parse chapter_failed SSE:', err);
      }
    });

    // 3. character_registered
    this.eventSource.addEventListener('character_registered', (e: MessageEvent) => {
      try {
        const raw: CharacterRegisteredEventPayload = JSON.parse(e.data);
        if (this.isEventStale(raw, gen)) return;
        this.advanceRevision(raw);

        const char: Character = {
          character_uuid: raw.character_uuid,
          canonical_name: raw.canonical_name,
          aliases: raw.aliases || [],
          is_narrator: Boolean(raw.is_narrator),
          voice_instruct: raw.voice_instruct || '',
          voice_status: mapWireVoiceStatus(raw.voice_status),
          voice_version: raw.voice_version || 1,
          preview_url: raw.preview_url || null,
          segment_count: raw.segment_count || 0,
        };
        useAppStore.getState().upsertCharacters([char]);
      } catch (err) {
        console.error('Failed to parse character_registered SSE:', err);
      }
    });

    // 4. character_voice_ready
    this.eventSource.addEventListener('character_voice_ready', (e: MessageEvent) => {
      try {
        const raw: CharacterVoiceReadyEventPayload = JSON.parse(e.data);
        if (this.isEventStale(raw, gen)) return;
        this.advanceRevision(raw);

        const char: Character = {
          character_uuid: raw.character_uuid,
          canonical_name: raw.canonical_name,
          aliases: raw.aliases || [],
          is_narrator: Boolean(raw.is_narrator),
          voice_instruct: raw.voice_instruct || '',
          voice_status: 'READY',
          voice_version: raw.voice_version || 1,
          preview_url: raw.preview_url,
          segment_count: raw.segment_count || 0,
        };
        useAppStore.getState().upsertCharacters([char]);
      } catch (err) {
        console.error('Failed to parse character_voice_ready SSE:', err);
      }
    });

    // 5. character_voice_failed
    this.eventSource.addEventListener('character_voice_failed', (e: MessageEvent) => {
      try {
        const raw: CharacterVoiceFailedEventPayload = JSON.parse(e.data);
        if (this.isEventStale(raw, gen)) return;
        this.advanceRevision(raw);

        useAppStore.getState().updateCharacterVoiceState(raw.character_uuid, {
          voice_status: 'FAILED',
        });
      } catch (err) {
        console.error('Failed to parse character_voice_failed SSE:', err);
      }
    });

    // 6. chunk_scripted
    this.eventSource.addEventListener('chunk_scripted', (e: MessageEvent) => {
      try {
        const data: ChunkScriptedEventPayload = JSON.parse(e.data);
        if (this.isEventStale(data, gen)) return;
        this.advanceRevision(data);

        const segments: ScriptSegment[] = data.segments.map((s, idx) => {
          const charId =
            ('character_uuid' in s && s.character_uuid)
              ? s.character_uuid
              : ('speaker_uuid' in s && s.speaker_uuid)
              ? s.speaker_uuid
              : 'char_narrator';
          const isNarrator = Boolean('is_narrator' in s ? s.is_narrator : useAppStore.getState().characters[charId]?.is_narrator);
          const isDial =
            'is_dialogue' in s && s.is_dialogue !== undefined
              ? Boolean(s.is_dialogue)
              : !isNarrator;

          const wireStatus = 'status' in s ? s.status : 'QUEUED';
          const audioStatus = mapWireSegmentStatus(wireStatus);

          return {
            segment_uuid: s.segment_uuid || `seg_${data.chunk_index}_${idx}`,
            segment_order: s.segment_order,
            chunk_index: s.chunk_index ?? data.chunk_index,
            paragraph_index: s.paragraph_index ?? null,
            is_paragraph_end: Boolean(s.is_paragraph_end),
            speaker_uuid: charId,
            speaker_name:
              ('speaker_name' in s && s.speaker_name)
                ? s.speaker_name
                : isNarrator
                ? 'Narrator'
                : 'Speaker',
            is_dialogue: isDial,
            text: s.text,
            emotion_hint: s.emotion_hint || null,
            audio_status: audioStatus,
            audio_url: ('audio_url' in s ? s.audio_url : null),
            duration_ms: ('duration_ms' in s ? s.duration_ms : null),
            voice_version: s.voice_version || 1,
            source_continuation: 'source_continuation' in s ? s.source_continuation : 0,
            error_message: 'error_message' in s ? s.error_message : null,
            last_error_code: 'last_error_code' in s ? s.last_error_code : null,
            retry_count: 'retry_count' in s ? s.retry_count : 0,
          };
        });

        const store = useAppStore.getState();
        store.appendChunkSegments(segments);

        const scriptedChunks = data.chunk_index + 1;
        const totalChunks = store.metadata?.total_chunks || store.pipeline.totalChunks || scriptedChunks;
        const totalSegments = Math.max(store.metadata?.total_segments || 0, data.total_segments_so_far);

        if (store.metadata) {
          store.setMetadata({
            total_segments: totalSegments,
            scripted_chunks: scriptedChunks,
          });
        }

        const readySegs = Object.values(store.segmentsByOrder).filter(
          (s) => s.audio_status === 'READY'
        ).length;
        const isCompleted = store.metadata?.review_phase === 'COMPLETED';

        store.setPipelineProgress({
          stage: isCompleted ? 'COMPLETED' : store.metadata?.review_phase==='SYNTHESIZING' ? 'SYNTHESIZING_AUDIO' : 'ANALYZING_SCRIPT',
          currentChunk: Math.min(scriptedChunks + 1, Math.max(totalChunks, 1)),
          totalChunks,
          totalSegments,
          readySegments: readySegs,
        });
      } catch (err) {
        console.error('Failed to parse chunk_scripted SSE:', err);
      }
    });

    // 7. segment_ready & segment_audio_ready
    const handleSegmentReady = (e: MessageEvent) => {
      try {
        const raw: SegmentAudioReadyEventPayload = JSON.parse(e.data);
        if (this.isEventStale(raw, gen)) return;
        this.advanceRevision(raw);

        const store = useAppStore.getState();
        const segOrder = raw.segment_order;
        const currentSeg = store.segmentsByOrder[segOrder];

        let audioUrl = raw.audio_url;
        const segUuid = raw.segment_uuid || currentSeg?.segment_uuid;
        const voiceVer = raw.voice_version || currentSeg?.voice_version || 1;

        if (!audioUrl || !audioUrl.startsWith('/api')) {
          if (segUuid) {
            audioUrl = `/api/v1/audio/segments/${segUuid}.mp3?v=${voiceVer}`;
          }
        }

        store.updateSegmentAudio(segOrder, {
          audio_status: 'READY',
          audio_url: audioUrl,
          duration_ms: raw.duration_ms,
          voice_version: voiceVer,
          error_message: null,
          last_error_code: null,
        });

        const readyCount = Object.values(useAppStore.getState().segmentsByOrder).filter(
          (s) => s.audio_status === 'READY'
        ).length;

        if (store.metadata) {
          store.setMetadata({ ready_segments: readyCount });
        }

        const totalSegs = Math.max(store.metadata?.total_segments || 0, store.pipeline.totalSegments, store.segmentOrders.length);
        const isCompleted = store.metadata?.review_phase === 'COMPLETED';

        store.setPipelineProgress({
          readySegments: readyCount,
          totalSegments: totalSegs,
          ...(isCompleted ? { stage: 'COMPLETED' as PipelineStage } : {}),
        });

        gaplessAudioEngine.onSegmentReady(segOrder);
      } catch (err) {
        console.error('Failed to parse segment ready SSE:', err);
      }
    };

    this.eventSource.addEventListener('segment_audio_ready', handleSegmentReady);
    this.eventSource.addEventListener('segment_ready', handleSegmentReady);

    // 8. segment_failed
    this.eventSource.addEventListener('segment_failed', (e: MessageEvent) => {
      try {
        const raw: SegmentFailedEventPayload = JSON.parse(e.data);
        if (this.isEventStale(raw, gen)) return;
        this.advanceRevision(raw);

        const store = useAppStore.getState();
        store.updateSegmentAudio(raw.segment_order, {
          audio_status: 'ERROR',
          error_message: raw.error_message,
          last_error_code: raw.error_code || null,
          retry_count: raw.retry_count,
        });
      } catch (err) {
        console.error('Failed to parse segment_failed SSE:', err);
      }
    });

    // 9. master_mp3_progress, chapter_mastering, chapter_completed
    const handleMasterProgress = (e: MessageEvent) => {
      try {
        const data: MasterMp3ProgressEventPayload | ChapterMasteringEventPayload | ChapterCompletedEventPayload = JSON.parse(e.data);
        if (this.isEventStale(data, gen)) return;
        this.advanceRevision(data);

        const store = useAppStore.getState();
        const progress =
          'progress_percent' in data
            ? data.progress_percent
            : data.status === 'COMPLETED'
            ? 100
            : 50;
        const isDone = progress >= 100 || data.status === 'COMPLETED';
        const masterUrl = 'master_mp3_url' in data ? data.master_mp3_url : store.metadata?.master_mp3_url;

        store.setMetadata({
          master_mp3_progress: progress,
          master_mp3_url: masterUrl || store.metadata?.master_mp3_url,
          status: isDone ? 'COMPLETED' : 'MASTERING',
          review_phase:isDone ? 'COMPLETED' : 'MASTERING',
        });

        if (isDone) {
          store.setPipelineProgress({ stage: 'COMPLETED' });
          this.disconnect(true);
        } else {
          store.setPipelineProgress({ stage: 'MASTERING' });
        }
      } catch (err) {
        console.error('Failed to parse master progress SSE:', err);
      }
    };

    this.eventSource.addEventListener('master_mp3_progress', handleMasterProgress);
    this.eventSource.addEventListener('chapter_mastering', handleMasterProgress);
    this.eventSource.addEventListener('chapter_completed', handleMasterProgress);

    // 10. snapshot_required
    this.eventSource.addEventListener('snapshot_required', (e: MessageEvent) => {
      try {
        const payload: SnapshotRequiredEventPayload = e.data ? JSON.parse(e.data) : {};
        if (payload.chapter_uuid && payload.chapter_uuid !== this.currentChapterId) return;
        if (this.currentChapterId) {
          this.fetchAndReconcileSnapshot(this.currentChapterId, gen);
        }
      } catch (err) {
        console.error('Failed to handle snapshot_required SSE:', err);
      }
    });

    // Error handling with exponential backoff
    this.eventSource.onerror = async () => {
      if (this.isExplicitlyClosed || gen !== this.connectionGeneration) return;

      const store = useAppStore.getState();
      const currentStatus = store.metadata?.status;

      // Close current socket
      if (this.eventSource) {
        this.eventSource.close();
        this.eventSource = null;
      }

      if (currentStatus === 'COMPLETED' || currentStatus === 'FAILED' || currentStatus === 'ERROR') {
        return;
      }

      const backoffDelays = [1000, 2000, 4000, 10000];
      const delay = backoffDelays[Math.min(this.reconnectAttempts, backoffDelays.length - 1)];
      this.reconnectAttempts++;

      this.reconnectTimer = setTimeout(async () => {
        if (!this.currentChapterId || this.isExplicitlyClosed || gen !== this.connectionGeneration) return;

        try {
          await this.fetchAndReconcileSnapshot(this.currentChapterId, gen);
        } catch (fetchErr) {
          console.warn('Reconciliation fetch failed during SSE reconnect:', fetchErr);
        }

        if (gen === this.connectionGeneration && !this.isExplicitlyClosed) {
          this.initEventSource(gen);
        }
      }, delay);
    };
  }

  reconcileSnapshot(snapshot: ChapterSnapshotResponse) {
    const meta=useAppStore.getState().metadata;
    if (meta?.chapter_id===snapshot.chapter_uuid && ((meta.run_generation ?? 0)>snapshot.run_generation ||
      ((meta.run_generation ?? 0)===snapshot.run_generation && (meta.state_revision ?? -1)>snapshot.state_revision))) return false;
    if ((this.currentChapterId===snapshot.chapter_uuid || meta?.chapter_id===snapshot.chapter_uuid) &&
      (snapshot.run_generation<this.currentRunGeneration || (snapshot.run_generation===this.currentRunGeneration && snapshot.state_revision<this.lastAppliedRevision))) return false;
    this.lastAppliedRevision = snapshot.state_revision;
    this.currentRunGeneration = snapshot.run_generation;

    const chars: Character[] = snapshot.characters.map((c) => ({
      reference_text: c.reference_text,
      character_uuid: c.character_uuid,
      canonical_name: c.canonical_name,
      aliases: c.aliases || [],
      is_narrator: Boolean(c.is_narrator),
      voice_instruct: c.voice_instruct || '',
      voice_status: mapWireVoiceStatus(c.voice_status),
      voice_version: c.voice_version || 1,
      preview_url: c.preview_url,
      segment_count: c.segment_count || 0,
    }));

    const charMap = new Map(snapshot.characters.map((c) => [c.character_uuid, c]));

    const segments: ScriptSegment[] = snapshot.segments.map((s) => {
      const char = charMap.get(s.character_uuid);
      const isNarrator = char
        ? Boolean(char.is_narrator)
        : Boolean(s.is_narrator);
      const speakerName = s.speaker_name || (char ? char.canonical_name : isNarrator ? 'Narrator' : 'Speaker');
      const isDialogue = s.is_dialogue !== undefined ? s.is_dialogue : !isNarrator;

      return {
        segment_uuid: s.segment_uuid,
        segment_order: s.segment_order,
        chunk_index: s.chunk_index ?? 0,
        paragraph_index: s.paragraph_index ?? null,
        is_paragraph_end: s.is_paragraph_end,
        speaker_uuid: s.character_uuid,
        speaker_name: speakerName,
        is_dialogue: isDialogue,
        text: s.text,
        emotion_hint: s.emotion_hint || null,
        audio_status: mapWireSegmentStatus(s.status),
        audio_url: s.audio_url,
        duration_ms: s.duration_ms,
        voice_version: s.voice_version,
        source_continuation: s.source_continuation ?? 0,
        error_message: s.error_message || null,
        last_error_code: s.last_error_code || null,
        retry_count: s.retry_count ?? 0,
      };
    });

    const isCompleted = snapshot.status === 'COMPLETED';
    const isFailed = snapshot.status === 'FAILED';
    const isMastering = snapshot.status === 'MASTERING';

    const totalChunks = snapshot.total_chunks ?? snapshot.metadata?.total_chunks ?? 0;
    const scriptedChunks = snapshot.scripted_chunks ?? snapshot.metadata?.scripted_chunks ?? 0;

    let stage: PipelineStage = 'IDLE';
    if (isFailed) {
      stage = 'FAILED';
    } else if (isCompleted) {
      stage = 'COMPLETED';
    } else if (isMastering) {
      stage = 'MASTERING';
    } else if (snapshot.review_phase==='AWAITING_APPROVAL') {
      stage = 'AWAITING_APPROVAL';
    } else if (snapshot.review_phase==='SYNTHESIZING') {
      stage = 'SYNTHESIZING_AUDIO';
    } else if (totalChunks > 0) {
      stage = 'ANALYZING_SCRIPT';
    } else {
      stage = 'FETCHING_METADATA';
    }

    const normalized: NormalizedChapterSnapshot = {
      metadata: {
        review_phase:snapshot.review_phase,review_revision:snapshot.review_revision,
        analysis_completed_at:snapshot.analysis_completed_at,approved_run_generation:snapshot.approved_run_generation,
        approved_review_revision:snapshot.approved_review_revision,approved_at:snapshot.approved_at,
        narration_authorized:snapshot.narration_authorized,can_approve:snapshot.can_approve,approval_blockers:snapshot.approval_blockers,
        work_id: snapshot.work_id || '',
        chapter_id: snapshot.chapter_uuid,
        source_url: snapshot.source_url || '',
        work_title: snapshot.work_title || 'AO3 Work',
        chapter_title: snapshot.chapter_title || 'Chapter',
        chapter_number: snapshot.chapter_number ?? 1,
        total_chapters: snapshot.total_chapters ?? null,
        author: snapshot.author || 'AO3 Author',
        total_segments: snapshot.total_segments,
        ready_segments: snapshot.ready_segments,
        master_mp3_progress: snapshot.master_mp3_progress ?? (isCompleted ? 100 : 0),
        master_mp3_url: snapshot.master_mp3_url,
        status: snapshot.status,
        total_chunks: totalChunks,
        scripted_chunks: scriptedChunks,
        error_message: snapshot.error_message || null,
        run_generation: snapshot.run_generation,
        state_revision: snapshot.state_revision,
      },
      characters: chars,
      candidates: snapshot.voice_preview_candidates,
      segments: segments,
      pipeline: {
        stage,
        currentChunk: Math.min(scriptedChunks + 1, Math.max(totalChunks, 1)),
        totalChunks,
        totalSegments: snapshot.total_segments,
        readySegments: snapshot.ready_segments,
      },
    };

    useAppStore.getState().replaceChapterSnapshot(normalized);

    if (isCompleted || isFailed) {
      this.disconnect(true);
    }
    return true;
  }

  disconnect(explicit = true) {
    this.isExplicitlyClosed = explicit;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
    if (explicit) {
      this.currentChapterId = null;
    }
  }
}

export const sseManager = new SSEManager();
