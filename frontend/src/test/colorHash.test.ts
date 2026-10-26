import { describe, it, expect } from 'vitest';
import { getCharacterTheme } from '../utils/colorHash';

describe('colorHash', () => {
  it('returns neutral slate theme for narrator', () => {
    const theme = getCharacterTheme('char_narrator', true);
    expect(theme.name).toBe('slate');
    expect(theme.quoteBg).toBe('bg-transparent');
  });

  it('returns neutral slate theme when UUID is narrator or contains narrator', () => {
    const theme1 = getCharacterTheme('narrator');
    const theme2 = getCharacterTheme('char_narrator_01');
    expect(theme1.name).toBe('slate');
    expect(theme2.name).toBe('slate');
  });

  it('deterministically hashes character UUID to a palette theme', () => {
    const uuidA = 'char_harry_potter';
    const uuidB = 'char_hermione_granger';

    const themeA1 = getCharacterTheme(uuidA);
    const themeA2 = getCharacterTheme(uuidA);
    const themeB = getCharacterTheme(uuidB);

    // Consistency
    expect(themeA1.name).toBe(themeA2.name);
    // Well-formed classes
    expect(themeA1.badgeBg).toContain('bg-');
    expect(themeA1.badgeText).toContain('text-');
    expect(themeB.badgeBg).toContain('bg-');
  });
});

