export type ChapterLifecycleStatus =
  | 'IDLE'
  | 'PENDING'
  | 'STREAMING'
  | 'MASTERING'
  | 'COMPLETED'
  | 'FAILED'
  | 'FETCHING_HTML'      // Scraping & sanitizing AO3 HTML (Spec 01)
  | 'SCRIPTING_CHUNKS'   // LLM generating chunks (Spec 02)
  | 'SYNTHESIZING_AUDIO' // TTS active on segments (Spec 03)
  | 'STITCHING_MASTER'   // FFmpeg concatenating master MP3 (Spec 03)
  | 'ERROR';

export type SegmentAudioStatus =
  | 'PENDING_TTS'        // Scripted by LLM, waiting in TTS PriorityQueue
  | 'SYNTHESIZING'       // Currently being processed by Qwen3-TTS worker
  | 'READY'              // Normalized audio file available at audio_url
  | 'STALE_VOICE'        // Character voice updated; awaiting re-synthesis
  | 'ERROR';             // TTS synthesis failed after retries

export type CharacterVoiceStatus =
  | 'PENDING'
  | 'GENERATING'
  | 'READY'
  | 'FAILED'
  | 'DESIGNING_REFERENCE' // Generating 5s reference via Qwen3-TTS-VoiceDesign
  | 'EXTRACTING_EMBEDDING'// Extracting .pt speaker embedding via Qwen3-TTS-Base
  | 'UPDATING'            // User triggered PATCH voice_instruct; re-designing
  | 'ERROR';

export interface WorkMetadata {
  review_phase?: ReviewPhase;
  review_revision?: number;
  analysis_completed_at?: string | null;
  approved_run_generation?: number | null;
  approved_review_revision?: number | null;
  approved_at?: string | null;
  narration_authorized?: boolean;
  can_approve?: boolean;
  approval_blockers?: {code: string; message: string}[];
  work_id: string;                // AO3 work ID (e.g., "12345678")
  chapter_id: string;             // Internal UUID or AO3 chapter ID
  source_url: string;
  work_title: string;
  chapter_title: string;
  chapter_number: number;
  total_chapters: number | null;
  author: string;
  status: ChapterLifecycleStatus;
  total_chunks: number;
  scripted_chunks: number;
  total_segments: number;         // Dynamic while status === 'SCRIPTING_CHUNKS', final thereafter
  ready_segments: number;
  master_mp3_progress: number;    // 0 to 100
  master_mp3_url: string | null;
  error_message: string | null;
  run_generation?: number;
  state_revision?: number;
}

export interface Character {
  reference_text?: string;
  character_uuid: string;
  canonical_name: string;
  aliases: string[];
  is_narrator: boolean;
  voice_instruct: string;         // Acoustic description for Qwen3-TTS-VoiceDesign
  voice_status: CharacterVoiceStatus;
  voice_version: number;          // Increments on every PATCH voice update for cache-busting
  preview_url: string | null;     // 5-second reference WAV/MP3 URL
  segment_count: number;
}

export interface ScriptSegment {
  segment_uuid: string;
  segment_order: number;          // 0-indexed strictly sequential integer across chapter
  chunk_index: number;            // 0 for fast-start chunk, 1..N for standard chunks
  paragraph_index: number | null; // 0-indexed paragraph grouping identifier or null if unknown
  is_paragraph_end: boolean;      // True if followed by 650ms pause; false for 250ms pause
  speaker_uuid: string;           // Foreign key -> Character.character_uuid
  speaker_name: string;
  is_dialogue: boolean;           // True for spoken quotes; false for narrator prose/attribution
  text: string;                   // Exact verbatim author text for this atomic segment
  emotion_hint?: string | null;   // Optional prosody tag from LLM
  audio_status: SegmentAudioStatus;
  audio_url: string | null;       // Relative/absolute URL to normalized segment audio
  duration_ms: number | null;     // Populated when audio_status === 'READY'
  voice_version: number;          // Matches character's voice_version used to synthesize
  source_continuation?: boolean | number;
  error_message?: string | null;
  last_error_code?: string | null;
  retry_count?: number;
}

export type PlaybackStatus =
  | 'IDLE'
  | 'PLAYING'
  | 'PAUSED'
  | 'WAITING_FOR_TTS'             // Playhead reached a segment where audio_status !== 'READY'
  | 'INTER_SEGMENT_PAUSE'         // Actively waiting out 250ms or 650ms silence timer
  | 'COMPLETED';

export type PlaybackSpeed = 0.75 | 1.0 | 1.25 | 1.5 | 2.0;

export interface ParagraphBlock {
  paragraph_index: number;
  segments: ScriptSegment[];
}

export type PipelineStage =
  | 'AWAITING_APPROVAL'
  | 'IDLE'               // No chapter loaded
  | 'FETCHING_METADATA'  // POST /api/v1/chapters/load in flight
  | 'ANALYZING_SCRIPT'   // LLM chunk deconstruction active
  | 'SYNTHESIZING_AUDIO' // TTS workers generating audio
  | 'MASTERING'          // FFmpeg mastering
  | 'COMPLETED'          // All chunks scripted & master MP3 ready
  | 'FAILED';            // Pipeline encountered unrecoverable failure

export interface PipelineProgress {
  stage: PipelineStage;
  currentChunk: number;        // 1-based index currently being analyzed
  totalChunks: number;
  totalSegments: number;
  readySegments: number;
  isDrawerExpanded: boolean;   // Sidebar pull tab open/close toggle
}

export interface NormalizedChapterSnapshot {
  candidates?: VoiceCandidate[];
  metadata: WorkMetadata;
  characters: Character[];
  segments: ScriptSegment[];
  pipeline?: Partial<PipelineProgress>;
}

export type ReviewPhase = 'ANALYZING' | 'AWAITING_APPROVAL' | 'SYNTHESIZING' | 'MASTERING' | 'COMPLETED' | 'FAILED';

export interface VoiceCandidate {
  candidate_uuid: string;
  character_uuid: string;
  run_generation: number;
  base_voice_version: number;
  voice_instruct: string;
  reference_text: string;
  status: 'PENDING' | 'GENERATING' | 'READY' | 'FAILED' | 'REVOKED';
  preview_url: string | null;
  error_message?: string | null;
}

export type ReaderTheme = 'dark' | 'sepia' | 'light';
export type ReaderFont = 'sans' | 'serif';

export interface ReaderPreferences {
  theme: ReaderTheme;
  font: ReaderFont;
  fontSize: number; // default 18
  isLibraryOpen: boolean;
  isCastOpen: boolean;
}

