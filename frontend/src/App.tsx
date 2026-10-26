import React, { useRef } from 'react';
import { UrlInputHeader } from './components/UrlInputHeader';
import { ScriptReader } from './components/ScriptReader';
import { VoiceSidebar } from './components/VoiceSidebar';
import { PlayerControls } from './components/PlayerControls';
import { useAppStore } from './store/useAppStore';
import { LibrarySidebar } from './components/LibrarySidebar';
import { openSavedChapter } from './services/chapterSelection';
import { ChapterReviewToolbar } from './components/ChapterReviewToolbar';

export const App: React.FC = () => {
  const voiceDialog = useRef<HTMLDialogElement>(null);
  const readerTheme = useAppStore((state) => state.readerTheme);
  const isCastOpen = useAppStore((state) => state.isCastOpen);
  const voiceRunKey = useAppStore((state) =>
    state.metadata ? `${state.metadata.chapter_id}:${state.metadata.run_generation ?? 0}` : 'empty'
  );

  return (
    <div
      id="app-body"
      className={`flex flex-col h-screen w-screen overflow-hidden font-story-sans antialiased transition-colors duration-200 ${
        readerTheme === 'sepia'
          ? 'theme-sepia bg-[#f4ecd8] text-[#3d3226]'
          : readerTheme === 'light'
          ? 'theme-light bg-[#fcfcfc] text-[#1c1c1e]'
          : 'bg-[#111215] text-[#edeef2]'
      }`}
    >
      {/* Top Bar / Masthead */}
      <UrlInputHeader />

      {/* Main Workspace */}
      <div className="relative flex flex-1 min-h-0 overflow-hidden">
        {/* Left: Bookshelf Sidebar */}
        <LibrarySidebar
          onSelect={async (chapter) => {
            await openSavedChapter(chapter.chapter_uuid);
          }}
        />

        {/* Center: Script Reader */}
        <main className="flex-1 min-w-0 overflow-hidden flex flex-col pt-14 xl:pt-0">
          <ChapterReviewToolbar />
          <ScriptReader />
        </main>

        {/* Right: Character Cast Greenroom */}
        {isCastOpen && (
          <div className="hidden lg:block h-full">
            <VoiceSidebar key={voiceRunKey} />
          </div>
        )}

        <button
          type="button"
          className="lg:hidden absolute right-3 top-3 z-10 rounded-xl border border-white/10 bg-[#16171d] px-3 py-2 text-xs font-semibold text-white shadow-lg cursor-pointer"
          onClick={() => voiceDialog.current?.showModal()}
        >
          Voices
        </button>
        <dialog
          ref={voiceDialog}
          aria-label="Character voices"
          className="m-auto max-h-[90vh] max-w-[95vw] overflow-auto border border-white/10 bg-[#16171d] text-stone-100 backdrop:bg-black/70 rounded-2xl shadow-2xl p-0"
        >
          <form method="dialog" className="flex justify-end p-2 border-b border-white/10">
            <button className="px-3 py-1 text-xs text-stone-300 hover:text-white cursor-pointer">
              Close voices
            </button>
          </form>
          <VoiceSidebar key={`mobile:${voiceRunKey}`} />
        </dialog>
      </div>

      {/* Floating Island Audio Transport */}
      <PlayerControls />
    </div>
  );
};

export default App;
