import React, { useState, useEffect, useRef } from 'react';
import {
  Download,
  Loader2,
  Play,
  RotateCw,
  AlertCircle,
  BookOpen,
  Headphones,
  Users,
  ChevronDown,
} from 'lucide-react';
import { parseAO3Url } from '../utils/ao3UrlParser';
import { apiClient } from '../services/apiClient';
import { sseManager } from '../services/sseManager';
import { useAppStore } from '../store/useAppStore';
import { RegenerateModal } from './RegenerateModal';
import { ForceRegenerateOption } from '../types/api';
import { beginChapterSelection, openSavedChapter } from '../services/chapterSelection';

export const UrlInputHeader: React.FC = () => {
  const [inputUrl, setInputUrl] = useState(() => {
    return localStorage.getItem('ao3_audiobook_last_url') || '';
  });
  const [isLoading, setIsLoading] = useState(false);
  const [isRetrying, setIsRetrying] = useState(false);
  const [isRegenOpen, setIsRegenOpen] = useState(false);
  const [isPrefsOpen, setIsPrefsOpen] = useState(false);
  const [errorBanner, setErrorBanner] = useState<string | null>(null);
  const prefsMenuRef = useRef<HTMLDivElement>(null);

  const isUrlBarOpen = useAppStore((state) => state.isUrlBarOpen);
  const toggleUrlBar = useAppStore((state) => state.toggleUrlBar);

  const metadata = useAppStore((state) => state.metadata);
  const characterIds = useAppStore((state) => state.characterIds);
  const hasFailedSegments = useAppStore((state) =>
    Object.values(state.segmentsByOrder).some((segment) => segment.audio_status === 'ERROR')
  );
  const setMetadata = useAppStore((state) => state.setMetadata);
  const setPlaybackStatus = useAppStore((state) => state.setPlaybackStatus);
  const setActiveSegmentOrder = useAppStore((state) => state.setActiveSegmentOrder);
  const autoplayEnabled = useAppStore((state) => state.autoplayEnabled);
  const setAutoplayEnabled = useAppStore((state) => state.setAutoplayEnabled);
  const setPipelineProgress = useAppStore((state) => state.setPipelineProgress);
  const pipelineStage = useAppStore((state) => state.pipeline.stage);

  // Reader Preferences
  const readerTheme = useAppStore((state) => state.readerTheme);
  const readerFont = useAppStore((state) => state.readerFont);
  const readerFontSize = useAppStore((state) => state.readerFontSize);
  const setReaderTheme = useAppStore((state) => state.setReaderTheme);
  const setReaderFont = useAppStore((state) => state.setReaderFont);
  const setReaderFontSize = useAppStore((state) => state.setReaderFontSize);
  const toggleLibraryDrawer = useAppStore((state) => state.toggleLibraryDrawer);
  const toggleCastDrawer = useAppStore((state) => state.toggleCastDrawer);

  // Close preferences dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (prefsMenuRef.current && !prefsMenuRef.current.contains(e.target as Node)) {
        setIsPrefsOpen(false);
      }
    };
    if (isPrefsOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isPrefsOpen]);

  // Restore snapshot on page mount if previously loaded
  useEffect(() => {
    const savedUuid = localStorage.getItem('ao3_audiobook_last_chapter_uuid');
    if (savedUuid && !metadata) {
      void openSavedChapter(savedUuid).catch((error) => {
        setErrorBanner(error instanceof Error ? error.message : 'Could not restore saved chapter');
      });
    }
  }, []);

  useEffect(() => {
    if (metadata?.source_url) setInputUrl(metadata.source_url);
  }, [metadata?.chapter_id, metadata?.source_url]);

  const parsed = parseAO3Url(inputUrl);
  const isInputNonEmpty = inputUrl.trim().length > 0;
  const isUrlInvalid = isInputNonEmpty && !parsed.isValid;

  const handleLoad = async (forceRegen: ForceRegenerateOption = 'NONE') => {
    if (!parsed.isValid || !parsed.canonicalUrl) return;

    setErrorBanner(null);
    setIsLoading(true);
    let isCurrent = () => true;

    try {
      isCurrent = beginChapterSelection();
      // Per Spec 07 Section 10.3: Call engine reset, disconnect SSE, and reset chapter state

      setPipelineProgress({
        stage: 'FETCHING_METADATA',
        currentChunk: 0,
        totalChunks: 0,
        totalSegments: 0,
        readySegments: 0,
      });

      // 2. Call ingest API
      const resp = await apiClient.ingestChapter(parsed.canonicalUrl, forceRegen);
      if (!isCurrent()) return;

      // Save for refresh persistence
      localStorage.setItem('ao3_audiobook_last_url', parsed.canonicalUrl);
      localStorage.setItem('ao3_audiobook_last_chapter_uuid', resp.chapter_uuid);

      // 3. Populate initial metadata
      setMetadata({
        work_id: resp.work_id,
        chapter_id: resp.chapter_uuid,
        source_url: parsed.canonicalUrl,
        work_title: resp.work_title || resp.title || 'AO3 Work',
        chapter_title: resp.chapter_title || `Chapter ${resp.chapter_number}`,
        chapter_number: resp.chapter_number,
        total_chapters: resp.total_chapters,
        author: resp.author || 'AO3 Author',
        status: resp.status,
        total_chunks: resp.total_chunks || 1,
        scripted_chunks: 0,
        total_segments: 0,
        ready_segments: 0,
        master_mp3_progress: 0,
        master_mp3_url: null,
        error_message: null,
        run_generation: resp.run_generation,
        state_revision: resp.state_revision,
        review_phase: 'ANALYZING',
        narration_authorized: false,
      });

      const isCompleted = resp.status === 'COMPLETED';
      setPipelineProgress({
        stage: isCompleted ? 'COMPLETED' : 'ANALYZING_SCRIPT',
        currentChunk: 1,
        totalChunks: resp.total_chunks || 1,
        totalSegments: 0,
        readySegments: 0,
      });

      // 4. Initial playback status based on autoplay toggle
      setActiveSegmentOrder(0);
      setPlaybackStatus('IDLE');
      toggleUrlBar(false);

      // 5. Connect SSE & reconcile snapshot
      await sseManager.connect(resp.chapter_uuid);
      if (!isCurrent()) return;
    } catch (err: any) {
      if (!isCurrent()) return;
      console.error('Failed to ingest chapter:', err);
      setErrorBanner(err.message || 'Failed to ingest AO3 chapter');
      setPlaybackStatus('IDLE');
      setPipelineProgress({ stage: 'IDLE' });
    } finally {
      setIsLoading(false);
    }
  };

  const handleRetryFailed = async () => {
    if (!metadata || isLoading || isRetrying) return;
    const { chapter_id, run_generation } = metadata;
    const isCurrent = () => {
      const current = useAppStore.getState().metadata;
      return current?.chapter_id === chapter_id && current.run_generation === run_generation;
    };
    setErrorBanner(null);
    setIsRetrying(true);
    try {
      const snapshot = await apiClient.retryFailed(chapter_id);
      if (!isCurrent()) return;
      sseManager.reconcileSnapshot(snapshot);
      await sseManager.connect(chapter_id, { saved: true });
    } catch (error) {
      if (isCurrent()) {
        setErrorBanner(error instanceof Error ? error.message : 'Failed to retry chapter');
      }
    } finally {
      setIsRetrying(false);
    }
  };

  const canRetryFailed =
    metadata &&
    metadata.approved_at &&
    metadata.approved_run_generation === metadata.run_generation &&
    metadata.approved_review_revision === metadata.review_revision &&
    (metadata.status === 'FAILED' || metadata.status === 'ERROR' || hasFailedSegments);

  const isMasterReady =
    metadata?.narration_authorized === true && Boolean(metadata?.master_mp3_url);

  const masterDownloadUrl = metadata?.chapter_id
    ? apiClient.getMasterDownloadUrl(metadata.chapter_id)
    : metadata?.master_mp3_url || '#';

  // Show dropdown when !metadata or explicitly opened
  const showUrlInput = !metadata || isUrlBarOpen;

  return (
    <header className="relative z-30 shrink-0 border-b border-white/[0.08] bg-[#16171d]/90 backdrop-blur-xl px-4 py-2.5 sm:px-6">
      <div className="w-full flex items-center justify-between gap-3 sm:gap-4">
        {/* Far Left: Brand Logo & Audiobook Studio text */}
        <div className="flex items-center gap-3 min-w-0">
          <div className="flex items-center gap-2.5 shrink-0">
            {/* Tangerine Brand Logo */}
            <div className="w-9 h-9 rounded-2xl bg-gradient-to-tr from-[#ff5a1f] to-[#ff8c42] flex items-center justify-center text-white shadow-[0_4px_16px_rgba(255,90,31,0.4)] shrink-0">
              <Headphones className="w-5 h-5 fill-current" />
            </div>
            <h1 className="text-sm font-bold tracking-tight text-white font-heading shrink-0">
              Audiobook Studio
            </h1>
          </div>

          {/* In its place: Fic title, Chapter Pill, and Byline */}
          {metadata ? (
            <div className="flex items-center gap-3 min-w-0 border-l border-white/10 pl-3 sm:pl-4">
              <div className="min-w-0">
                <div className="flex items-center gap-2 min-w-0">
                  <span
                    className="text-sm font-bold tracking-tight text-white truncate max-w-[180px] sm:max-w-sm md:max-w-md"
                    title={metadata.work_title}
                  >
                    {metadata.work_title || 'AO3 Work'}
                  </span>
                  <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-[#ff5a1f]/15 text-[#ff7a45] border border-[#ff5a1f]/30 shrink-0">
                    Ch. {metadata.chapter_number}
                  </span>
                </div>
                <p className="text-xs text-stone-400 truncate">
                  by <span className="text-stone-300 font-medium">{metadata.author || 'AO3 Author'}</span>
                  {' • '}
                  <span className="text-stone-300 font-medium">{metadata.total_segments || 0} segments</span>
                </p>
              </div>
            </div>
          ) : (
            <div className="border-l border-white/10 pl-3 sm:pl-4 min-w-0 hidden sm:block">
              <p className="text-xs text-stone-400 truncate">
                Multi-Character Voiced Synthesis
              </p>
            </div>
          )}
        </div>

        {/* Right Controls: Bookshelf, Aa Menu, Cast, Audio Actions */}
        <div className="flex items-center gap-2 shrink-0">
          {/* Toggle Bookshelf Button */}
          <button
            id="toggle-library-btn"
            type="button"
            onClick={() => toggleLibraryDrawer()}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold text-white bg-white/[0.08] hover:bg-white/[0.14] border border-white/10 transition-all cursor-pointer"
          >
            <BookOpen className="w-4 h-4 text-[#ff7a45]" />
            <span className="hidden sm:inline">My Bookshelf</span>
          </button>

          {/* Reading Preferences Popover Toggle ("Aa" Menu) */}
          <div className="relative" ref={prefsMenuRef}>
            <button
              id="reader-prefs-btn"
              type="button"
              onClick={() => setIsPrefsOpen(!isPrefsOpen)}
              className="flex items-center gap-1 px-3 py-1.5 rounded-xl text-xs font-semibold text-stone-300 hover:text-white bg-white/[0.06] hover:bg-white/[0.1] transition-all cursor-pointer"
              title="Reading Preferences"
            >
              <span className="font-bold">Aa</span>
              <ChevronDown className="w-3.5 h-3.5 text-stone-400" />
            </button>

            {/* Preferences Dropdown Menu */}
            {isPrefsOpen && (
              <div
                id="prefs-menu"
                className="absolute right-0 mt-2 w-64 p-3.5 rounded-2xl bg-[#1c1e24] border border-white/10 shadow-2xl z-50 space-y-3 animate-in fade-in zoom-in-95 duration-100"
              >
                <div>
                  <span className="text-[11px] font-bold uppercase tracking-wider text-stone-400">
                    Reading Font
                  </span>
                  <div className="grid grid-cols-2 gap-1.5 mt-1.5">
                    <button
                      id="font-sans-btn"
                      type="button"
                      onClick={() => setReaderFont('sans')}
                      className={`px-2.5 py-1.5 rounded-xl text-xs font-semibold transition-all ${
                        readerFont === 'sans'
                          ? 'bg-[#ff5a1f] text-white shadow-sm'
                          : 'bg-white/[0.06] text-stone-300 hover:bg-white/[0.1]'
                      }`}
                    >
                      Modern Sans
                    </button>
                    <button
                      id="font-serif-btn"
                      type="button"
                      onClick={() => setReaderFont('serif')}
                      className={`px-2.5 py-1.5 rounded-xl text-xs font-medium font-story-serif transition-all ${
                        readerFont === 'serif'
                          ? 'bg-[#ff5a1f] text-white shadow-sm'
                          : 'bg-white/[0.06] text-stone-300 hover:bg-white/[0.1]'
                      }`}
                    >
                      Cozy Book
                    </button>
                  </div>
                </div>

                <div>
                  <span className="text-[11px] font-bold uppercase tracking-wider text-stone-400">
                    Text Size
                  </span>
                  <div className="flex items-center justify-between gap-2 mt-1.5 bg-black/30 p-1.5 rounded-xl border border-white/5">
                    <button
                      id="font-smaller-btn"
                      type="button"
                      onClick={() => setReaderFontSize(readerFontSize - 1)}
                      className="w-8 h-7 rounded-lg bg-white/[0.06] hover:bg-white/[0.12] text-xs font-bold text-stone-300"
                    >
                      A-
                    </button>
                    <span id="font-size-label" className="text-xs font-semibold text-stone-300">
                      {readerFontSize}px
                    </span>
                    <button
                      id="font-larger-btn"
                      type="button"
                      onClick={() => setReaderFontSize(readerFontSize + 1)}
                      className="w-8 h-7 rounded-lg bg-white/[0.06] hover:bg-white/[0.12] text-xs font-bold text-stone-300"
                    >
                      A+
                    </button>
                  </div>
                </div>

                <div>
                  <span className="text-[11px] font-bold uppercase tracking-wider text-stone-400">
                    Theme
                  </span>
                  <div className="grid grid-cols-3 gap-1.5 mt-1.5">
                    <button
                      id="theme-dark-btn"
                      type="button"
                      onClick={() => setReaderTheme('dark')}
                      className={`py-1.5 rounded-xl text-[11px] font-semibold transition-all ${
                        readerTheme === 'dark' ? 'bg-[#ff5a1f] text-white' : 'bg-white/[0.06] text-stone-300'
                      }`}
                    >
                      Dark
                    </button>
                    <button
                      id="theme-sepia-btn"
                      type="button"
                      onClick={() => setReaderTheme('sepia')}
                      className={`py-1.5 rounded-xl text-[11px] font-semibold transition-all ${
                        readerTheme === 'sepia'
                          ? 'bg-[#ff5a1f] text-white'
                          : 'bg-[#f4ecd8] text-[#3d3226]'
                      }`}
                    >
                      Sepia
                    </button>
                    <button
                      id="theme-light-btn"
                      type="button"
                      onClick={() => setReaderTheme('light')}
                      className={`py-1.5 rounded-xl text-[11px] font-semibold transition-all ${
                        readerTheme === 'light'
                          ? 'bg-[#ff5a1f] text-white'
                          : 'bg-white text-stone-900'
                      }`}
                    >
                      Light
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Cast Drawer Toggle */}
          <button
            id="toggle-cast-btn"
            type="button"
            onClick={() => toggleCastDrawer()}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold text-[#ff7a45] bg-[#ff5a1f]/15 border border-[#ff5a1f]/30 hover:bg-[#ff5a1f]/25 transition-all cursor-pointer"
          >
            <Users className="w-4 h-4" />
            <span className="hidden sm:inline">Voice Cast ({characterIds.length})</span>
          </button>

          {/* Autoplay / Click-to-Play Toggle */}
          <button
            type="button"
            onClick={() => setAutoplayEnabled(!autoplayEnabled)}
            title={
              autoplayEnabled
                ? 'Autoplay: ON (Audio plays automatically as each portion finishes synthesis)'
                : 'Click to Play: ON (Audio buffers silently; click Play manually when ready to listen)'
            }
            className={`hidden lg:flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-xl border transition-all cursor-pointer ${
              autoplayEnabled
                ? 'bg-emerald-950/70 border-emerald-500/40 text-emerald-300 shadow-sm hover:bg-emerald-900/60'
                : 'bg-white/[0.06] border-white/10 text-stone-300 hover:text-white hover:bg-white/[0.1]'
            }`}
          >
            <div
              className={`w-2 h-2 rounded-full transition-colors ${
                autoplayEnabled ? 'bg-emerald-400 animate-pulse' : 'bg-stone-500'
              }`}
            />
            <span>{autoplayEnabled ? 'Autoplay' : 'Click to Play'}</span>
          </button>

          {/* Retry Failed button */}
          {(canRetryFailed || isRetrying) && (
            <button
              type="button"
              onClick={handleRetryFailed}
              disabled={isLoading || isRetrying}
              className="flex items-center gap-2 px-3 py-1.5 bg-[#1e2029] hover:bg-[#282a36] disabled:opacity-40 disabled:cursor-not-allowed text-stone-200 text-xs font-semibold rounded-xl border border-white/10 transition-all cursor-pointer"
            >
              {isRetrying ? <Loader2 className="w-4 h-4 animate-spin text-[#ff7a45]" /> : <RotateCw className="w-4 h-4 text-[#ff7a45]" />}
              <span>{isRetrying ? 'Retrying...' : 'Retry Failed'}</span>
            </button>
          )}

          {/* Master MP3 Download */}
          {metadata && isMasterReady && (
            <a
              href={masterDownloadUrl}
              download
              className="flex items-center gap-2 px-3.5 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-bold rounded-xl shadow-md shadow-emerald-600/20 transition-all"
            >
              <Download className="w-4 h-4" />
              <span className="hidden sm:inline">Master MP3</span>
            </a>
          )}
        </div>
      </div>

      {/* Collapsible / Dropdown AO3 URL Input Bar */}
      <div
        id="url-dropdown"
        className={`w-full max-w-3xl mx-auto mt-3 p-3 rounded-2xl bg-[#191a20] border border-white/10 shadow-2xl flex items-center gap-2 transition-all duration-200 ${
          showUrlInput ? 'flex' : 'hidden'
        }`}
      >
        <div className="relative flex-1">
          <input
            type="text"
            value={inputUrl}
            onChange={(e) => setInputUrl(e.target.value)}
            placeholder="https://archiveofourown.org/works/..."
            className={`w-full bg-[#101114] border text-xs rounded-xl px-4 py-2.5 text-stone-100 placeholder-stone-500 focus:outline-none transition-all ${
              isUrlInvalid
                ? 'border-red-500/80 focus:border-red-500'
                : 'border-stone-700/80 focus:border-[#ff5a1f]'
            }`}
          />
          {isUrlInvalid && (
            <div className="absolute right-3 top-2.5 flex items-center gap-1 text-xs text-red-400 font-medium">
              <AlertCircle className="w-4 h-4" />
              <span className="hidden sm:inline">Invalid AO3 URL</span>
            </div>
          )}
        </div>

        {/* Load & Analyze Button */}
        <button
          type="button"
          onClick={() => handleLoad('NONE')}
          disabled={!parsed.isValid || isLoading || isRetrying}
          className="flex items-center gap-2 px-4 py-2.5 bg-[#ff5a1f] hover:bg-[#ff7038] disabled:bg-stone-800 disabled:text-stone-500 text-white text-xs font-bold rounded-xl shadow-lg shadow-[#ff5a1f]/20 transition-all shrink-0 cursor-pointer disabled:cursor-not-allowed"
        >
          {isLoading ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Play className="w-4 h-4 fill-current" />
          )}
          <span>Load & Analyze</span>
        </button>

        {/* Regenerate Button */}
        {metadata && (
          <button
            type="button"
            onClick={() => setIsRegenOpen(true)}
            disabled={isLoading || isRetrying}
            title="Regenerate Chapter"
            className="p-2.5 bg-white/[0.06] hover:bg-white/[0.12] disabled:opacity-40 text-stone-300 hover:text-white rounded-xl border border-white/10 transition-all shrink-0 cursor-pointer"
          >
            <RotateCw className="w-4 h-4" />
          </button>
        )}

        {/* Close Dropdown Button (when chapter is loaded) */}
        {metadata && (
          <button
            id="close-url-btn"
            type="button"
            onClick={() => toggleUrlBar(false)}
            className="p-2 text-stone-400 hover:text-white text-sm cursor-pointer"
            title="Close URL Bar"
          >
            ✕
          </button>
        )}
      </div>

      {/* Error banner if load fails */}
      {errorBanner && (
        <div role="alert" className="max-w-4xl mx-auto mt-2 p-2.5 rounded-xl bg-red-950/80 border border-red-800/60 text-red-200 text-xs flex items-center justify-between">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-red-400 shrink-0" />
            <span>{errorBanner}</span>
          </div>
          <button
            type="button"
            onClick={() => setErrorBanner(null)}
            className="text-red-400 hover:text-white px-1 font-bold cursor-pointer"
          >
            ✕
          </button>
        </div>
      )}

      {/* Ingestion Loading Modal */}
      {isLoading && (
        <div
          role="dialog"
          aria-label="Loading chapter"
          aria-describedby="chapter-load-progress"
          className="fixed inset-x-4 top-24 z-50 mx-auto max-w-lg rounded-2xl border border-[#ff5a1f]/40 bg-[#191a20] p-6 shadow-2xl"
        >
          <h2 className="flex items-center gap-2 text-lg font-bold text-white font-heading">
            <Loader2 aria-hidden="true" className="h-5 w-5 animate-spin text-[#ff7a45]" />
            Loading chapter
          </h2>
          <p id="chapter-load-progress" role="status" className="mt-3 text-sm text-stone-300">
            {pipelineStage === 'FETCHING_METADATA'
              ? 'Fetching chapter text and metadata from AO3...'
              : 'Connecting to chapter progress...'}
          </p>
          <progress aria-label="Chapter loading progress" className="mt-4 w-full accent-[#ff5a1f]" />
        </div>
      )}

      {/* Regenerate Scope Modal */}
      <RegenerateModal
        isOpen={isRegenOpen}
        onClose={() => setIsRegenOpen(false)}
        onConfirm={(opt) => handleLoad(opt)}
      />
    </header>
  );
};
