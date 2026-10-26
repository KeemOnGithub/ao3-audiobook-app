import { beforeEach, describe, expect, it } from 'vitest';
import { useAppStore } from '../store/useAppStore';

describe('Reader preferences store slice', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('initializes with default preferences', () => {
    const state = useAppStore.getState();
    expect(state.readerTheme).toBe('dark');
    expect(state.readerFont).toBe('sans');
    expect(state.readerFontSize).toBe(18);
  });

  it('updates theme and persists to localStorage', () => {
    useAppStore.getState().setReaderTheme('sepia');
    expect(useAppStore.getState().readerTheme).toBe('sepia');
    const stored = JSON.parse(localStorage.getItem('ao3_reader_prefs') || '{}');
    expect(stored.theme).toBe('sepia');

    useAppStore.getState().setReaderTheme('light');
    expect(useAppStore.getState().readerTheme).toBe('light');
    const stored2 = JSON.parse(localStorage.getItem('ao3_reader_prefs') || '{}');
    expect(stored2.theme).toBe('light');
  });

  it('updates font and persists to localStorage', () => {
    useAppStore.getState().setReaderFont('serif');
    expect(useAppStore.getState().readerFont).toBe('serif');
    const stored = JSON.parse(localStorage.getItem('ao3_reader_prefs') || '{}');
    expect(stored.font).toBe('serif');
  });

  it('updates font size with clamping between 15 and 26', () => {
    useAppStore.getState().setReaderFontSize(22);
    expect(useAppStore.getState().readerFontSize).toBe(22);

    useAppStore.getState().setReaderFontSize(10);
    expect(useAppStore.getState().readerFontSize).toBe(15);

    useAppStore.getState().setReaderFontSize(35);
    expect(useAppStore.getState().readerFontSize).toBe(26);
  });

  it('toggles library and cast drawers', () => {
    const initialLib = useAppStore.getState().isLibraryOpen;
    useAppStore.getState().toggleLibraryDrawer();
    expect(useAppStore.getState().isLibraryOpen).toBe(!initialLib);
    useAppStore.getState().toggleLibraryDrawer(true);
    expect(useAppStore.getState().isLibraryOpen).toBe(true);

    const initialCast = useAppStore.getState().isCastOpen;
    useAppStore.getState().toggleCastDrawer();
    expect(useAppStore.getState().isCastOpen).toBe(!initialCast);
    useAppStore.getState().toggleCastDrawer(false);
    expect(useAppStore.getState().isCastOpen).toBe(false);
  });
});

