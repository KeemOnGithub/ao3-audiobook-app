import { useEffect, useId, useRef, useState } from 'react';
import { BookOpen, ChevronDown, ChevronRight, Loader2, X, Search, Plus } from 'lucide-react';
import { apiClient } from '../services/apiClient';
import { useAppStore } from '../store/useAppStore';
import type { LibraryChapter, LibraryResponse } from '../types/api';

interface LibrarySidebarProps {
  onSelect: (chapter: LibraryChapter) => Promise<void>;
}

const chapterStatuses: Record<string, { text: string; color: string }> = {
  ANALYZING: { text: 'Analyzing complete chapter', color: 'bg-orange-400' },
  AWAITING_APPROVAL: { text: 'Awaiting review and approval', color: 'bg-indigo-400' },
  SYNTHESIZING: { text: 'Generating approved narration', color: 'bg-orange-400' },
  PENDING: { text: 'Queued for processing', color: 'bg-orange-400' },
  STREAMING: { text: 'Processing', color: 'bg-orange-400' },
  MASTERING: { text: 'Finalizing audio', color: 'bg-orange-400' },
  COMPLETED: { text: 'Audio processing complete', color: 'bg-emerald-400' },
  FAILED: { text: 'Processing failed or interrupted', color: 'bg-red-400' },
};

const SPINE_GRADIENTS = [
  'from-[#ff5a1f] via-[#d9480f] to-[#7f1d1d]',
  'from-[#4f46e5] via-[#3b82f6] to-[#0284c7]',
  'from-[#059669] via-[#10b981] to-[#047857]',
  'from-[#7c3aed] via-[#8b5cf6] to-[#6d28d9]',
  'from-[#db2777] via-[#ec4899] to-[#be185d]',
  'from-[#d97706] via-[#f59e0b] to-[#b45309]',
];

function getWorkGradient(workId: string): string {
  let hash = 0;
  for (let i = 0; i < workId.length; i++) {
    hash = (hash << 5) - hash + workId.charCodeAt(i);
    hash |= 0;
  }
  const idx = Math.abs(hash) % SPINE_GRADIENTS.length;
  return SPINE_GRADIENTS[idx];
}

function getWorkInitials(title: string): string {
  const words = title.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return 'AO3';
  if (words.length === 1) return words[0].slice(0, 3).toUpperCase();
  return words.slice(0, 3).map((w) => w[0]).join('').toUpperCase();
}

export function LibrarySidebar({ onSelect }: LibrarySidebarProps) {
  const activeChapterId = useAppStore((state) => state.metadata?.chapter_id);
  const isLibraryOpen = useAppStore((state) => state.isLibraryOpen);
  const isUrlBarOpen = useAppStore((state) => state.isUrlBarOpen);
  const toggleUrlBar = useAppStore((state) => state.toggleUrlBar);
  const [library, setLibrary] = useState<LibraryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectionError, setSelectionError] = useState<string | null>(null);
  const [selecting, setSelecting] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [filterQuery, setFilterQuery] = useState('');
  const [drawerOpen, setDrawerOpen] = useState(false);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const selectionVersion = useRef(0);
  const refreshRef = useRef<() => void>(() => {});
  const id = useId();

  useEffect(() => {
    let disposed = false;
    let inFlight = false;
    let queued = false;
    let timer: ReturnType<typeof setInterval> | undefined;
    const refresh = async (queue = false) => {
      if (inFlight) {
        queued ||= queue;
        return;
      }
      inFlight = true;
      setLoading(true);
      try {
        const response = await apiClient.getLibrary();
        if (!disposed) {
          setLibrary(response);
          const currentActive = useAppStore.getState().metadata?.chapter_id;
          const activeWork = response.works.find((work) =>
            work.chapters.some((chapter) => chapter.chapter_uuid === currentActive),
          );
          if (activeWork) {
            setExpanded((previous) => new Set(previous).add(activeWork.work_id));
          }
          setError(null);
        }
      } catch {
        if (!disposed) setError('Unable to refresh the library. Please try again.');
      } finally {
        inFlight = false;
        if (!disposed) {
          setLoading(false);
          if (queued) {
            queued = false;
            void refresh();
          }
        }
      }
    };
    refreshRef.current = () => { void refresh(); };
    const onFocus = () => {
      if (document.visibilityState !== 'hidden') void refresh();
    };
    const updateTimer = () => {
      clearInterval(timer);
      if (document.visibilityState !== 'hidden') {
        timer = setInterval(() => { void refresh(); }, 30_000);
      }
    };
    const onVisibility = () => {
      updateTimer();
      onFocus();
    };
    const unsubscribe = useAppStore.subscribe(
      (state) => [state.metadata?.chapter_id, state.metadata?.run_generation, state.metadata?.review_phase] as const,
      (current, previous) => {
        if (current[0] && current.some((value, index) => value !== previous[index])) void refresh(true);
      },
      { equalityFn: (current, previous) => current.every((value, index) => value === previous[index]) },
    );
    void refresh();
    updateTimer();
    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      disposed = true;
      clearInterval(timer);
      unsubscribe();
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, []);

  useEffect(() => {
    const activeWork = library?.works.find((work) =>
      work.chapters.some((chapter) => chapter.chapter_uuid === activeChapterId),
    );
    if (activeWork) setExpanded((previous) => new Set(previous).add(activeWork.work_id));
  }, [library, activeChapterId]);

  useEffect(() => () => { selectionVersion.current += 1; }, []);

  useEffect(() => {
    if (!drawerOpen) return;
    const dialog = dialogRef.current;
    if (!dialog) return;
    dialog.showModal();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    dialog.querySelector<HTMLButtonElement>('button')?.focus();
    const media = window.matchMedia('(min-width: 1280px)');
    const onResize = () => { if (media.matches) setDrawerOpen(false); };
    media.addEventListener('change', onResize);
    onResize();
    return () => {
      dialog.close();
      document.body.style.overflow = previousOverflow;
      media.removeEventListener('change', onResize);
      toggleRef.current?.focus();
    };
  }, [drawerOpen]);

  const selectChapter = async (chapter: LibraryChapter) => {
    const version = ++selectionVersion.current;
    setSelecting(chapter.chapter_uuid);
    setSelectionError(null);
    try {
      await onSelect(chapter);
      if (version === selectionVersion.current) setDrawerOpen(false);
    } catch (cause) {
      if (version === selectionVersion.current) {
        setSelectionError(cause instanceof Error ? cause.message : 'Unable to open this chapter. Please try again.');
      }
    } finally {
      if (version === selectionVersion.current) setSelecting(null);
    }
  };

  const storyCount = library?.works.length || 0;

  const filteredWorks = (library?.works || []).filter((work) => {
    if (!filterQuery.trim()) return true;
    const q = filterQuery.toLowerCase();
    const matchesWork = work.title.toLowerCase().includes(q) || work.author.toLowerCase().includes(q);
    const matchesChapter = work.chapters.some((ch) =>
      ch.chapter_title.toLowerCase().includes(q) || String(ch.chapter_number).includes(q)
    );
    return matchesWork || matchesChapter;
  });

  const handleAddChapterClick = () => {
    const next = !isUrlBarOpen;
    toggleUrlBar(next);
    if (next) {
      setTimeout(() => {
        const input = document.querySelector<HTMLInputElement>('input[placeholder*="archiveofourown"]');
        if (input) {
          input.focus();
          input.scrollIntoView({ behavior: 'smooth' });
        }
      }, 50);
    }
  };

  const content = (
    <>
      {/* Bookshelf Header & Search */}
      <div className="p-4 border-b border-white/[0.08] space-y-3 shrink-0">
        <div className="flex items-center justify-between">
          <h2 id={`${id}-heading`} className="flex items-center gap-2 text-sm font-bold tracking-tight text-white font-heading">
            <BookOpen aria-hidden="true" className="h-4 w-4 text-[#ff7a45]" />
            <span>Library</span>
          </h2>
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-bold px-2 py-0.5 rounded-full bg-white/[0.08] text-stone-300">
              {storyCount} {storyCount === 1 ? 'Story' : 'Stories'}
            </span>
            {drawerOpen && (
              <button
                type="button"
                aria-label="Close library"
                onClick={() => setDrawerOpen(false)}
                className="rounded p-1 text-stone-400 hover:text-white focus-visible:ring-2 focus-visible:ring-[#ff5a1f]"
              >
                <X aria-hidden="true" className="h-4 w-4" />
              </button>
            )}
          </div>
        </div>

        {/* Filter Search Input */}
        <div className="relative">
          <input
            type="text"
            value={filterQuery}
            onChange={(e) => setFilterQuery(e.target.value)}
            placeholder="Filter saved fics or chapters..."
            className="w-full bg-[#0d0e11] border border-white/[0.08] rounded-xl pl-3 pr-8 py-1.5 text-xs text-stone-200 placeholder-stone-500 focus:outline-none focus:border-[#ff5a1f] transition-all"
          />
          <Search className="w-3.5 h-3.5 absolute right-2.5 top-2 text-stone-500 pointer-events-none" />
        </div>
      </div>

      {/* Main Bookshelf List */}
      <div className="min-h-0 flex-1 overflow-y-auto p-3 space-y-3">
        {loading && (
          <p role="status" className="flex items-center gap-2 p-2 text-xs text-stone-400">
            <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin text-[#ff7a45]" />
            {library ? 'Refreshing library...' : 'Loading library...'}
          </p>
        )}
        {error && (
          <div role="alert" className="p-3 rounded-xl bg-amber-950/40 border border-amber-500/20 text-xs text-amber-300 space-y-2">
            <p>{error}</p>
            <button
              type="button"
              onClick={() => refreshRef.current()}
              className="rounded underline font-semibold focus-visible:ring-2 focus-visible:ring-[#ff5a1f]"
            >
              Retry library
            </button>
          </div>
        )}
        {selectionError && (
          <p role="alert" className="p-2 text-xs text-amber-300 bg-amber-950/40 rounded-xl border border-amber-500/20">
            {selectionError}
          </p>
        )}
        {library?.works.length === 0 && (
          <p className="p-3 text-xs text-stone-400 text-center">
            No saved chapters yet. Imported chapters will appear here.
          </p>
        )}
        {filteredWorks.map((work, index) => {
          const open = expanded.has(work.work_id) || Boolean(filterQuery.trim());
          const groupId = `${id}-work-${index}`;
          const gradient = getWorkGradient(work.work_id);
          const initials = getWorkInitials(work.title);
          const completedCount = work.chapters.filter((c) => c.status === 'COMPLETED').length;

          return (
            <section
              key={work.work_id}
              className={`rounded-2xl border transition-all ${
                open ? 'border-white/15 bg-[#171820]' : 'border-white/[0.08] bg-[#14151b] hover:border-white/15'
              } p-3 space-y-2.5 shadow-sm`}
            >
              {/* Accordion trigger button */}
              <button
                type="button"
                aria-expanded={open}
                aria-controls={groupId}
                onClick={() =>
                  setExpanded((previous) => {
                    const next = new Set(previous);
                    if (next.has(work.work_id)) next.delete(work.work_id);
                    else next.add(work.work_id);
                    return next;
                  })
                }
                className="flex w-full items-start gap-3 text-left focus-visible:ring-2 focus-visible:ring-[#ff5a1f] rounded-lg group"
              >
                {/* Mini Stylized Book Spine */}
                <div
                  className={`w-9 h-13 rounded-lg bg-gradient-to-br ${gradient} flex flex-col justify-between p-1 text-white shadow-md shrink-0 border border-white/20`}
                >
                  <span className="text-[7px] font-bold tracking-tighter opacity-80 uppercase">AO3</span>
                  <span className="text-[8px] font-extrabold leading-tight truncate">{initials}</span>
                </div>

                {/* Work Details */}
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between">
                    <span className="block break-words text-sm font-medium text-slate-100 group-hover:text-white transition-colors">
                      {work.title}
                    </span>
                    {open ? (
                      <ChevronDown aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-stone-400" />
                    ) : (
                      <ChevronRight aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-stone-400" />
                    )}
                  </div>
                  <span className="block break-words text-xs text-slate-400">
                    {work.author}
                  </span>
                  <span className="block text-xs text-slate-500 mt-0.5">
                    {work.chapters.length} saved {work.chapters.length === 1 ? 'chapter' : 'chapters'}
                  </span>

                  {/* Visual progress bar */}
                  <div className="flex items-center gap-2 mt-1.5">
                    <div className="flex-1 h-1 bg-white/10 rounded-full overflow-hidden">
                      <div
                        className="h-full bg-[#ff5a1f]"
                        style={{
                          width: `${work.chapters.length > 0 ? Math.min(100, Math.round((completedCount / work.chapters.length) * 100)) : 0}%`,
                        }}
                      />
                    </div>
                  </div>
                </div>
              </button>

              {/* Saved Chapters Accordion Content */}
              <ul id={groupId} hidden={!open} className="pt-2 border-t border-white/[0.06] space-y-1">
                {work.chapters.map((chapter) => {
                  const status =
                    chapterStatuses[chapter.review_phase || chapter.status] ?? {
                      text: 'Unknown processing status',
                      color: 'bg-slate-400',
                    };
                  const isActive = chapter.chapter_uuid === activeChapterId;

                  return (
                    <li key={chapter.chapter_uuid}>
                      <button
                        type="button"
                        aria-current={isActive ? 'true' : undefined}
                        aria-busy={selecting === chapter.chapter_uuid}
                        onClick={() => {
                          void selectChapter(chapter);
                        }}
                        className={`my-1 w-full rounded-xl p-2 text-left text-xs focus-visible:ring-2 focus-visible:ring-[#ff5a1f] transition-all flex flex-col gap-1 ${
                          isActive
                            ? 'bg-indigo-500/15 text-indigo-300 border border-indigo-500/30 shadow-sm'
                            : 'text-stone-300 hover:bg-white/[0.06]'
                        }`}
                      >
                        <div className="flex items-center justify-between gap-2 w-full">
                          <span className="flex items-start gap-2 min-w-0" title={status.text}>
                            <span
                              aria-hidden="true"
                              className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${status.color}`}
                            />
                            <span className="min-w-0 break-words font-medium">
                              Chapter {chapter.chapter_number}: {chapter.chapter_title}
                            </span>
                            <span className="sr-only">{status.text}</span>
                          </span>

                          {/* Friendly Status Badges */}
                          <div className="flex items-center gap-1 shrink-0">
                            {isActive ? (
                              <span className="text-[10px] font-bold text-[#ff7a45] bg-[#ff5a1f]/20 px-1.5 py-0.5 rounded border border-[#ff5a1f]/30">
                                Playing Now
                              </span>
                            ) : chapter.status === 'COMPLETED' ? (
                              <span className="text-[10px] text-emerald-400 font-semibold bg-emerald-950/40 px-1.5 py-0.5 rounded border border-emerald-500/20">
                                Ready
                              </span>
                            ) : chapter.review_phase === 'AWAITING_APPROVAL' || chapter.review_phase === 'ANALYZING' ? (
                              <span className="text-[10px] text-amber-300 font-medium bg-amber-950/40 px-1.5 py-0.5 rounded border border-amber-500/20">
                                In Review
                              </span>
                            ) : (
                              <span className="text-[10px] text-orange-300 font-medium bg-orange-950/40 px-1.5 py-0.5 rounded border border-orange-500/20">
                                Processing
                              </span>
                            )}
                          </div>
                        </div>

                        {chapter.status === 'COMPLETED' && !chapter.master_audio_available && (
                          <span className="block text-xs text-amber-300 pl-4">
                            Master audio unavailable
                          </span>
                        )}
                        {selecting === chapter.chapter_uuid && (
                          <span role="status" className="block text-xs text-indigo-300 pl-4">
                            Opening chapter...
                          </span>
                        )}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </section>
          );
        })}
      </div>

      {/* Quick Add Another Chapter Footer Button - visible on desktop bookshelf */}
      {!drawerOpen && (
        <div className="p-3 border-t border-white/[0.08] bg-[#121317] shrink-0">
          <button
            type="button"
            aria-expanded={isUrlBarOpen}
            onClick={handleAddChapterClick}
            className={`w-full py-2.5 rounded-xl text-xs font-bold flex items-center justify-center gap-2 border transition-all cursor-pointer ${
              isUrlBarOpen
                ? 'bg-[#ff5a1f]/15 border-[#ff5a1f]/40 text-[#ff7a45] shadow-sm'
                : 'bg-white/[0.06] hover:bg-white/[0.12] border-white/5 text-stone-200'
            }`}
          >
            <Plus className={`w-3.5 h-3.5 transition-transform duration-200 ${isUrlBarOpen ? 'rotate-45 text-[#ff7a45]' : 'text-[#ff7a45]'}`} />
            <span>Add Another AO3 Chapter</span>
          </button>
        </div>
      )}
    </>
  );

  return (
    <>
      <button
        ref={toggleRef}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={drawerOpen}
        aria-controls={`${id}-drawer`}
        onClick={() => setDrawerOpen(true)}
        className="absolute left-3 top-3 z-20 flex items-center gap-2 rounded-xl border border-white/10 bg-[#15161c] px-3 py-2 text-xs font-semibold text-white shadow-lg focus-visible:ring-2 focus-visible:ring-[#ff5a1f] xl:hidden cursor-pointer"
      >
        <BookOpen aria-hidden="true" className="h-4 w-4 text-[#ff7a45]" />
        <span>Library</span>
      </button>

      {!drawerOpen && isLibraryOpen && (
        <aside
          aria-labelledby={`${id}-heading`}
          className="hidden h-full min-h-0 w-80 shrink-0 flex-col border-r border-white/[0.08] bg-[#15161c] shadow-xl xl:flex z-20"
        >
          {content}
        </aside>
      )}

      {drawerOpen && (
        <dialog
          ref={dialogRef}
          id={`${id}-drawer`}
          aria-labelledby={`${id}-heading`}
          onCancel={(event) => {
            event.preventDefault();
            setDrawerOpen(false);
          }}
          onClick={(event) => {
            if (event.target === event.currentTarget) setDrawerOpen(false);
          }}
          onKeyDown={(event) => {
            if (event.key === 'Escape') {
              event.preventDefault();
              setDrawerOpen(false);
            }
            if (event.key !== 'Tab') return;
            const buttons = Array.from(
              event.currentTarget.querySelectorAll<HTMLButtonElement>('button')
            ).filter((button) => !button.disabled && !button.closest('[hidden]'));
            const first = buttons[0];
            const last = buttons[buttons.length - 1];
            if (event.shiftKey && document.activeElement === first) {
              event.preventDefault();
              last?.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
              event.preventDefault();
              first?.focus();
            }
          }}
          className="fixed inset-y-0 left-0 right-auto m-0 h-[100dvh] max-h-none w-80 max-w-[90vw] border-r border-white/10 bg-[#15161c] p-0 text-stone-200 backdrop:bg-black/60 shadow-2xl"
        >
          <div className="flex h-full min-h-0 flex-col">{content}</div>
        </dialog>
      )}
    </>
  );
}
