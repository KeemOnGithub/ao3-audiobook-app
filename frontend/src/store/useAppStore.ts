import { create } from 'zustand';
import { subscribeWithSelector } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';
import {
  Character,
  NormalizedChapterSnapshot,
  PlaybackSpeed,
  PlaybackStatus,
  PipelineProgress,
  PipelineStage,
  ScriptSegment,
  WorkMetadata,
  VoiceCandidate,
  ReaderTheme,
  ReaderFont,
  ReaderPreferences,
} from '../types/domain';

export type { ReaderTheme, ReaderFont, ReaderPreferences };

export interface AppStoreState {
  selectedSegmentIds: string[];
  highlightedCharacterId: string | null;
  toggleSegmentSelection: (id: string) => void;
  clearSegmentSelection: () => void;
  setHighlightedCharacter: (id: string | null) => void;
  voiceCandidates: Record<string, VoiceCandidate>;
  voiceDrafts: Record<string, {prompt: string; sample: string; stale?: boolean}>;
  setVoiceDraft: (id: string, draft: {prompt: string; sample: string}) => void;
  clearVoiceDraft: (id?: string) => void;
  reviewBusy: boolean;
  reviewError: string | null;
  setReviewMutation: (busy: boolean, error?: string | null) => void;
  // 1. WorkMetadataSlice
  metadata: WorkMetadata | null;
  setMetadata: (meta: Partial<WorkMetadata>) => void;
  resetWork: () => void;
  resetChapterState: () => void;
  replaceChapterSnapshot: (snapshot: NormalizedChapterSnapshot) => void;
  setPipelineFailure: (message: string, code?: string) => void;

  // 2. CharactersSlice
  characters: Record<string, Character>;
  characterIds: string[];
  upsertCharacters: (chars: Character[]) => void;
  updateCharacterVoiceState: (
    character_uuid: string,
    patch: Partial<Character>
  ) => void;

  // 3. ScriptSegmentsSlice
  segmentsByOrder: Record<number, ScriptSegment>;
  segmentOrders: number[];
  appendChunkSegments: (segments: ScriptSegment[]) => void;
  updateSegmentAudio: (
    segment_order: number,
    patch: Partial<ScriptSegment>
  ) => void;
  markCharacterSegmentsStale: (character_uuid: string) => void;

  // 4. PlaybackSlice
  playbackStatus: PlaybackStatus;
  activeSegmentOrder: number;
  currentSegmentElapsedMs: number;
  playbackSpeed: PlaybackSpeed;
  autoScrollEnabled: boolean;
  autoplayEnabled: boolean;
  activePreviewCharacterId: string | null;

  // Playback Actions
  setPlaybackStatus: (status: PlaybackStatus) => void;
  setActiveSegmentOrder: (order: number) => void;
  setCurrentSegmentElapsedMs: (ms: number) => void;
  setPlaybackSpeed: (speed: PlaybackSpeed) => void;
  setAutoScrollEnabled: (enabled: boolean) => void;
  setAutoplayEnabled: (enabled: boolean) => void;
  setActivePreviewCharacterId: (id: string | null) => void;

  // 5. PipelineSlice
  pipeline: PipelineProgress;
  setPipelineProgress: (patch: Partial<PipelineProgress>) => void;
  togglePipelineDrawer: (expanded?: boolean) => void;

  // 6. ReaderPreferencesSlice
  readerTheme: ReaderTheme;
  readerFont: ReaderFont;
  readerFontSize: number;
  isLibraryOpen: boolean;
  isCastOpen: boolean;
  isUrlBarOpen: boolean;
  setReaderTheme: (theme: ReaderTheme) => void;
  setReaderFont: (font: ReaderFont) => void;
  setReaderFontSize: (size: number) => void;
  toggleLibraryDrawer: (open?: boolean) => void;
  toggleCastDrawer: (open?: boolean) => void;
  toggleUrlBar: (open?: boolean) => void;
}

const initialPlaybackState = {
  playbackStatus: 'IDLE' as PlaybackStatus,
  activeSegmentOrder: 0,
  currentSegmentElapsedMs: 0,
  playbackSpeed: 1.0 as PlaybackSpeed,
  autoScrollEnabled: true,
  autoplayEnabled: typeof window !== 'undefined' ? localStorage.getItem('ao3_audiobook_autoplay') !== 'false' : true,
  activePreviewCharacterId: null as string | null,
};

const initialPipelineState: PipelineProgress = {
  stage: 'IDLE',
  currentChunk: 0,
  totalChunks: 0,
  totalSegments: 0,
  readySegments: 0,
  isDrawerExpanded: false,
};

const READER_PREFS_KEY = 'ao3_reader_prefs';

function loadSavedReaderPrefs(): Partial<ReaderPreferences> {
  if (typeof window === 'undefined') return {};
  try {
    const raw = localStorage.getItem(READER_PREFS_KEY);
    if (!raw) return {};
    return JSON.parse(raw) as Partial<ReaderPreferences>;
  } catch {
    return {};
  }
}

function persistReaderPrefs(patch: Partial<ReaderPreferences>) {
  if (typeof window === 'undefined') return;
  try {
    const current = loadSavedReaderPrefs();
    localStorage.setItem(READER_PREFS_KEY, JSON.stringify({ ...current, ...patch }));
  } catch {
    // Ignore storage errors in restricted contexts
  }
}

const hasSavedChapter =
  typeof window !== 'undefined' && Boolean(localStorage.getItem('ao3_audiobook_last_chapter_uuid'));

const savedReaderPrefs = loadSavedReaderPrefs();
const initialReaderPreferences = {
  readerTheme: savedReaderPrefs.theme || ('dark' as ReaderTheme),
  readerFont: savedReaderPrefs.font || ('sans' as ReaderFont),
  readerFontSize: typeof savedReaderPrefs.fontSize === 'number' ? savedReaderPrefs.fontSize : 18,
  isLibraryOpen: typeof savedReaderPrefs.isLibraryOpen === 'boolean' ? savedReaderPrefs.isLibraryOpen : true,
  isCastOpen: typeof savedReaderPrefs.isCastOpen === 'boolean' ? savedReaderPrefs.isCastOpen : false,
  isUrlBarOpen: !hasSavedChapter,
};

export const useAppStore = create<AppStoreState>()(
  subscribeWithSelector(
    immer((set) => ({
      selectedSegmentIds: [], highlightedCharacterId: null,
      toggleSegmentSelection: id => set(state => { state.selectedSegmentIds = state.selectedSegmentIds.includes(id) ? state.selectedSegmentIds.filter(value => value !== id) : [...state.selectedSegmentIds,id]; }),
      clearSegmentSelection: () => set(state => { state.selectedSegmentIds = []; }),
      setHighlightedCharacter: id => set(state => { state.highlightedCharacterId = id; }),
      voiceCandidates: {}, voiceDrafts: {}, reviewBusy: false, reviewError: null,
      setVoiceDraft: (id,draft) => set((state) => { state.voiceDrafts[id] = draft; }),
      clearVoiceDraft: (id) => set((state) => { if (id) delete state.voiceDrafts[id]; else state.voiceDrafts = {}; }),
      setReviewMutation: (busy,error = null) => set((state) => { state.reviewBusy=busy;state.reviewError=error; }),
      // Metadata
      metadata: null,
      setMetadata: (patch) =>
        set((state) => {
          if (!state.metadata) {
            state.metadata = patch as WorkMetadata;
          } else {
            Object.assign(state.metadata, patch);
          }
        }),
      resetChapterState: () =>
        set((state) => {
          state.metadata = null;
          state.selectedSegmentIds = []; state.highlightedCharacterId = null;
          state.voiceCandidates = {}; state.voiceDrafts = {}; state.reviewError = null;
          state.characters = {};
          state.characterIds = [];
          state.segmentsByOrder = {};
          state.segmentOrders = [];
          state.playbackStatus = 'IDLE';
          state.activeSegmentOrder = 0;
          state.currentSegmentElapsedMs = 0;
          state.activePreviewCharacterId = null;
          state.pipeline = { ...initialPipelineState };
        }),
      resetWork: () =>
        set((state) => {
          state.metadata = null;
          state.selectedSegmentIds = []; state.highlightedCharacterId = null;
          state.voiceCandidates = {}; state.voiceDrafts = {}; state.reviewError = null;
          state.characters = {};
          state.characterIds = [];
          state.segmentsByOrder = {};
          state.segmentOrders = [];
          state.playbackStatus = 'IDLE';
          state.activeSegmentOrder = 0;
          state.currentSegmentElapsedMs = 0;
          state.activePreviewCharacterId = null;
          state.pipeline = { ...initialPipelineState };
        }),
      setPipelineFailure: (message: string, _code?: string) =>
        set((state) => {
          if (state.metadata) {
            state.metadata.status = 'FAILED';
            state.metadata.error_message = message;
          }
          state.pipeline.stage = 'FAILED';
          if (
            state.playbackStatus === 'WAITING_FOR_TTS' ||
            state.playbackStatus === 'PLAYING' ||
            state.playbackStatus === 'INTER_SEGMENT_PAUSE'
          ) {
            state.playbackStatus = 'PAUSED';
          }
        }),
      replaceChapterSnapshot: (snapshot: NormalizedChapterSnapshot) =>
        set((state) => {
          // 1. Atomically replace metadata
          if (state.metadata?.chapter_id===snapshot.metadata.chapter_id && (
            (state.metadata.run_generation ?? 0)>(snapshot.metadata.run_generation ?? 0) ||
            (state.metadata.run_generation===snapshot.metadata.run_generation && (state.metadata.state_revision ?? -1)>(snapshot.metadata.state_revision ?? -1)))) return;
          if (state.metadata?.chapter_id !== snapshot.metadata.chapter_id || state.metadata?.run_generation !== snapshot.metadata.run_generation) {
            state.voiceDrafts = {};
            state.selectedSegmentIds = []; state.highlightedCharacterId = null;
          }
          state.metadata = { ...snapshot.metadata };
          state.voiceCandidates = Object.fromEntries((snapshot.candidates || []).map(c => [c.candidate_uuid,c]));

          // 2. Atomically replace characters and rebuild IDs (removes absent characters)
          const newChars: Record<string, Character> = {};
          for (const c of snapshot.characters) {
            newChars[c.character_uuid] = { ...c };
          }
          state.characters = newChars;

          const allIds = Object.keys(newChars);
          allIds.sort((a, b) => {
            const charA = newChars[a];
            const charB = newChars[b];
            if (charA.is_narrator && !charB.is_narrator) return -1;
            if (!charA.is_narrator && charB.is_narrator) return 1;
            return a.localeCompare(b);
          });
          state.characterIds = allIds;

          // 3. Atomically replace segments (removes absent segments)
          const newSegments: Record<number, ScriptSegment> = {};
          const orders: number[] = [];
          for (const seg of snapshot.segments) {
            newSegments[seg.segment_order] = { ...seg };
            orders.push(seg.segment_order);
          }
          state.segmentsByOrder = newSegments;
          state.segmentOrders = orders.sort((a, b) => a - b);

          // 4. Safely clamp activeSegmentOrder
          if (state.segmentOrders.length > 0) {
            const maxOrder = state.segmentOrders[state.segmentOrders.length - 1];
            const minOrder = state.segmentOrders[0];
            if (state.activeSegmentOrder > maxOrder) {
              state.activeSegmentOrder = maxOrder;
            } else if (state.activeSegmentOrder < minOrder) {
              state.activeSegmentOrder = minOrder;
            }
          } else {
            state.activeSegmentOrder = 0;
          }

          // 5. Pipeline counters & stage reconciliation
          const status = snapshot.metadata.status;
          const isCompleted = status === 'COMPLETED';
          const isFailed = status === 'FAILED' || status === 'ERROR';
          const isMastering = status === 'MASTERING' || status === 'STITCHING_MASTER';

          let stage: PipelineStage = 'IDLE';
          if (isFailed) {
            stage = 'FAILED';
          } else if (isCompleted) {
            stage = 'COMPLETED';
          } else if (isMastering) {
            stage = 'MASTERING';
          } else if (snapshot.metadata.review_phase==='AWAITING_APPROVAL') {
            stage='AWAITING_APPROVAL';
          } else if (snapshot.metadata.review_phase==='SYNTHESIZING') {
            stage='SYNTHESIZING_AUDIO';
          } else if (snapshot.pipeline?.stage) {
            stage = snapshot.pipeline.stage;
          } else {
            const totalChunks = snapshot.metadata.total_chunks || 0;
            if (totalChunks > 0) {
              stage = 'ANALYZING_SCRIPT';
            } else {
              stage = 'FETCHING_METADATA';
            }
          }

          const totalSegments = snapshot.metadata.total_segments || snapshot.segments.length;
          const readySegments =
            snapshot.metadata.ready_segments ||
            snapshot.segments.filter((s) => s.audio_status === 'READY').length;

          state.pipeline = {
            ...state.pipeline,
            totalChunks: snapshot.metadata.total_chunks || snapshot.pipeline?.totalChunks || 0,
            currentChunk: Math.min(
              (snapshot.metadata.scripted_chunks || 0) + 1,
              Math.max(snapshot.metadata.total_chunks || 1, 1)
            ),
            totalSegments,
            readySegments,
            ...(snapshot.pipeline || {}),
            stage, // enforce failure/completion precedence
          };

          if (isFailed && (state.playbackStatus === 'PLAYING' || state.playbackStatus === 'WAITING_FOR_TTS')) {
            state.playbackStatus = 'PAUSED';
          }
        }),

      // Characters
      characters: {},
      characterIds: [],
      upsertCharacters: (chars) =>
        set((state) => {
          for (const c of chars) {
            state.characters[c.character_uuid] = {
              ...(state.characters[c.character_uuid] || {}),
              ...c,
            };
          }
          // Sort IDs: Narrator first, then by existing or appearance order
          const allIds = Object.keys(state.characters);
          allIds.sort((a, b) => {
            const charA = state.characters[a];
            const charB = state.characters[b];
            if (charA.is_narrator && !charB.is_narrator) return -1;
            if (!charA.is_narrator && charB.is_narrator) return 1;
            return a.localeCompare(b);
          });
          state.characterIds = allIds;
        }),
      updateCharacterVoiceState: (character_uuid, patch) =>
        set((state) => {
          if (state.characters[character_uuid]) {
            Object.assign(state.characters[character_uuid], patch);
          }
        }),

      // Script Segments
      segmentsByOrder: {},
      segmentOrders: [],
      appendChunkSegments: (newSegments) =>
        set((state) => {
          const ordersSet = new Set(state.segmentOrders);
          for (const seg of newSegments) {
            state.segmentsByOrder[seg.segment_order] = {
              ...(state.segmentsByOrder[seg.segment_order] || {}),
              ...seg,
            };
            ordersSet.add(seg.segment_order);
          }
          state.segmentOrders = Array.from(ordersSet).sort((a, b) => a - b);
          if (state.metadata) {
            state.metadata.total_segments = Math.max(
              state.metadata.total_segments,
              state.segmentOrders.length
            );
          }
        }),
      updateSegmentAudio: (segment_order, patch) =>
        set((state) => {
          const existing = state.segmentsByOrder[segment_order];
          if (existing) {
            Object.assign(existing, patch);
          }
        }),
      markCharacterSegmentsStale: (character_uuid) =>
        set((state) => {
          for (const order of state.segmentOrders) {
            const seg = state.segmentsByOrder[order];
            if (seg && seg.speaker_uuid === character_uuid) {
              seg.audio_status = 'STALE_VOICE';
            }
          }
        }),

      // Playback
      ...initialPlaybackState,
      setPlaybackStatus: (status) =>
        set((state) => {
          state.playbackStatus = status;
        }),
      setActiveSegmentOrder: (order) =>
        set((state) => {
          state.activeSegmentOrder = order;
        }),
      setCurrentSegmentElapsedMs: (ms) =>
        set((state) => {
          state.currentSegmentElapsedMs = ms;
        }),
      setPlaybackSpeed: (speed) =>
        set((state) => {
          state.playbackSpeed = speed;
        }),
      setAutoScrollEnabled: (enabled) =>
        set((state) => {
          state.autoScrollEnabled = enabled;
        }),
      setAutoplayEnabled: (enabled) =>
        set((state) => {
          state.autoplayEnabled = enabled;
          if (typeof window !== 'undefined') {
            localStorage.setItem('ao3_audiobook_autoplay', String(enabled));
          }
        }),
      setActivePreviewCharacterId: (id) =>
        set((state) => {
          state.activePreviewCharacterId = id;
        }),

      // Pipeline
      pipeline: { ...initialPipelineState },
      setPipelineProgress: (patch) =>
        set((state) => {
          Object.assign(state.pipeline, patch);
        }),
      togglePipelineDrawer: (expanded) =>
        set((state) => {
          state.pipeline.isDrawerExpanded =
            expanded !== undefined ? expanded : !state.pipeline.isDrawerExpanded;
        }),

      // Reader Preferences
      readerTheme: initialReaderPreferences.readerTheme,
      readerFont: initialReaderPreferences.readerFont,
      readerFontSize: initialReaderPreferences.readerFontSize,
      isLibraryOpen: initialReaderPreferences.isLibraryOpen,
      isCastOpen: initialReaderPreferences.isCastOpen,
      isUrlBarOpen: initialReaderPreferences.isUrlBarOpen,
      setReaderTheme: (theme: ReaderTheme) =>
        set((state) => {
          state.readerTheme = theme;
          persistReaderPrefs({ theme });
        }),
      setReaderFont: (font: ReaderFont) =>
        set((state) => {
          state.readerFont = font;
          persistReaderPrefs({ font });
        }),
      setReaderFontSize: (size: number) =>
        set((state) => {
          const clamped = Math.min(26, Math.max(15, size));
          state.readerFontSize = clamped;
          persistReaderPrefs({ fontSize: clamped });
        }),
      toggleLibraryDrawer: (open?: boolean) =>
        set((state) => {
          const next = open !== undefined ? open : !state.isLibraryOpen;
          state.isLibraryOpen = next;
          persistReaderPrefs({ isLibraryOpen: next });
        }),
      toggleCastDrawer: (open?: boolean) =>
        set((state) => {
          const next = open !== undefined ? open : !state.isCastOpen;
          state.isCastOpen = next;
          persistReaderPrefs({ isCastOpen: next });
        }),
      toggleUrlBar: (open?: boolean) =>
        set((state) => {
          state.isUrlBarOpen = open !== undefined ? open : !state.isUrlBarOpen;
        }),
    }))
  )
);
