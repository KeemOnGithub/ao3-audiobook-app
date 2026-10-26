import {
  ScriptSegment,
  WorkMetadata,
  VoiceCandidate,
  ReviewPhase,
} from './domain';

export type ForceRegenerateOption = 'NONE' | 'AUDIO_ONLY' | 'FULL';

export type ChapterWireStatus =
  | 'PENDING'
  | 'STREAMING'
  | 'MASTERING'
  | 'COMPLETED'
  | 'FAILED';

export type SegmentWireStatus =
  | 'QUEUED'
  | 'SYNTHESIZING'
  | 'READY'
  | 'FAILED';

export type VoiceWireStatus =
  | 'PENDING'
  | 'GENERATING'
  | 'READY'
  | 'FAILED';

export interface ChapterIngestRequest {
  url: string;
  force_regenerate?: ForceRegenerateOption | boolean;
}

export interface ChapterLoadResponse {
  chapter_uuid: string;
  work_id: string;
  chapter_id: string;
  title: string;
  work_title: string;
  chapter_title: string | null;
  chapter_number: number;
  total_chapters: number | null;
  author: string;
  status: ChapterWireStatus;
  total_chunks: number;
  run_generation: number;
  state_revision: number;
}

export interface CharacterApiSchema {
  reference_text?: string;
  character_uuid: string;
  character_key?: string;
  canonical_name: string;
  voice_instruct: string;
  voice_version: number;
  preview_url: string | null;
  aliases?: string[];
  is_narrator?: boolean;
  voice_status?: VoiceWireStatus;
  segment_count?: number;
  artifact_provider?: string | null;
  artifact_fingerprint?: string | null;
}

export interface SegmentApiSchema {
  segment_uuid: string;
  segment_order: number;
  character_uuid: string;
  speaker_name?: string;
  is_narrator?: boolean;
  is_dialogue?: boolean;
  text: string;
  status: SegmentWireStatus;
  error_message?: string | null;
  last_error_code?: string | null;
  retry_count?: number;
  audio_url: string | null;
  duration_ms: number | null;
  is_paragraph_end: boolean;
  voice_version: number;
  chunk_index?: number;
  paragraph_index?: number | null;
  source_continuation?: boolean | number;
  emotion_hint?: string | null;
}

export interface ChapterSnapshotResponse {
  review_phase?: ReviewPhase;
  review_revision?: number;
  analysis_completed_at?: string | null;
  approved_run_generation?: number | null;
  approved_review_revision?: number | null;
  approved_at?: string | null;
  narration_authorized?: boolean;
  can_approve?: boolean;
  approval_blockers?: {code: string; message: string}[];
  voice_preview_candidates?: VoiceCandidate[];
  chapter_uuid: string;
  work_id?: string | null;
  work_title?: string | null;
  chapter_title?: string | null;
  chapter_number?: number | null;
  total_chapters?: number | null;
  author?: string | null;
  source_url?: string | null;
  status: ChapterWireStatus;
  error_message?: string | null;
  run_generation: number;
  state_revision: number;
  total_chunks?: number;
  scripted_chunks?: number;
  master_mp3_ready: boolean;
  master_mp3_url: string | null;
  master_mp3_progress?: number;
  total_segments: number;
  ready_segments: number;
  characters: CharacterApiSchema[];
  segments: SegmentApiSchema[];
  metadata?: Partial<WorkMetadata>;
}

export interface PlaybackSeekPayload {
  target_segment_order: number;
}

export interface PlaybackSeekResponse {
  status: string;
  active_head_order?: number;
  prioritized_from?: number;
}

export interface CharacterVoiceUpdatePayload {
  chapter_id: string;
  voice_instruct: string;
}

export interface CharacterVoiceUpdateResponse {
  character_uuid: string;
  voice_version: number;
  preview_url: string;
  affected_segments_count: number;
}

export interface MasterDownloadProgressResponse {
  status: string;
  progress_pct: number;
  total_segments: number;
  processed_segments: number;
  estimated_remaining_seconds?: number | null;
}

export interface RetryFailedRequest {
  segment_uuids?: string[];
}

// SSE Event types
export interface BaseSSEEventEnvelope {
  chapter_uuid: string;
  run_generation: number;
  state_revision: number;
}

export interface ChapterStatusEventPayload extends BaseSSEEventEnvelope {
  status: ChapterWireStatus;
  error_message?: string | null;
  total_chunks?: number;
  scripted_chunks?: number;
  total_segments?: number;
  ready_segments?: number;
}

export interface ChapterFailedEventPayload extends BaseSSEEventEnvelope {
  status: 'FAILED';
  error_code?: string | null;
  error_message: string;
}

export interface CharacterRegisteredEventPayload extends BaseSSEEventEnvelope, CharacterApiSchema {}

export interface CharacterVoiceReadyEventPayload extends BaseSSEEventEnvelope, CharacterApiSchema {
  preview_url: string;
}

export interface CharacterVoiceFailedEventPayload extends BaseSSEEventEnvelope {
  character_uuid: string;
  voice_status: 'FAILED';
  error_code?: string | null;
  error_message: string;
}

export interface ChunkScriptedEventPayload extends BaseSSEEventEnvelope {
  chunk_index: number;
  total_chunks: number;
  total_segments_so_far: number;
  segments: (ScriptSegment | SegmentApiSchema)[];
}

export interface SegmentAudioReadyEventPayload extends BaseSSEEventEnvelope {
  segment_order: number;
  segment_uuid: string;
  audio_url: string;
  duration_ms: number;
  voice_version: number;
}

export interface SegmentFailedEventPayload extends BaseSSEEventEnvelope {
  segment_order: number;
  segment_uuid: string;
  status: 'FAILED';
  retry_count: number;
  error_code?: string | null;
  error_message: string;
}

export interface ChapterMasteringEventPayload extends BaseSSEEventEnvelope {
  status: 'MASTERING';
  progress_percent: number;
}

export interface MasterMp3ProgressEventPayload extends BaseSSEEventEnvelope {
  status?: string;
  progress_percent: number;
  master_mp3_url?: string | null;
}

export interface ChapterCompletedEventPayload extends BaseSSEEventEnvelope {
  status: 'COMPLETED';
  master_mp3_url: string;
}

export interface SnapshotRequiredEventPayload {
  chapter_uuid?: string;
  reason?: string;
}
export interface LibraryChapter {
  review_phase?: ReviewPhase;
  chapter_uuid: string;
  chapter_id: string;
  chapter_number: number;
  chapter_title: string;
  source_url: string;
  status: ChapterWireStatus;
  master_audio_available: boolean;
}

export interface ReviewIdentity {
  expected_run_generation: number;
  expected_review_revision: number;
}

export interface LibraryResponse {
  works: {
    work_id: string;
    title: string;
    author: string;
    chapters: LibraryChapter[];
  }[];
}
