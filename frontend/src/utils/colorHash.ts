export interface CharacterTheme {
  name: string;
  badgeBg: string;
  badgeText: string;
  badgeBorder: string;
  quoteText: string;
  quoteBg: string;
  ring: string;
  dot: string;
}

const NARRATOR_THEME: CharacterTheme = {
  name: 'slate',
  badgeBg: 'bg-slate-800',
  badgeText: 'text-slate-300',
  badgeBorder: 'border-slate-700',
  quoteText: 'text-slate-200',
  quoteBg: 'bg-transparent',
  ring: 'ring-slate-500',
  dot: 'bg-slate-400',
};

const PALETTE: CharacterTheme[] = [
  {
    name: 'sky',
    badgeBg: 'bg-sky-950/80',
    badgeText: 'text-sky-300',
    badgeBorder: 'border-sky-700/60',
    quoteText: 'text-sky-100',
    quoteBg: 'bg-sky-950/20',
    ring: 'ring-sky-400',
    dot: 'bg-sky-400',
  },
  {
    name: 'emerald',
    badgeBg: 'bg-emerald-950/80',
    badgeText: 'text-emerald-300',
    badgeBorder: 'border-emerald-700/60',
    quoteText: 'text-emerald-100',
    quoteBg: 'bg-emerald-950/20',
    ring: 'ring-emerald-400',
    dot: 'bg-emerald-400',
  },
  {
    name: 'amber',
    badgeBg: 'bg-amber-950/80',
    badgeText: 'text-amber-300',
    badgeBorder: 'border-amber-700/60',
    quoteText: 'text-amber-100',
    quoteBg: 'bg-amber-950/20',
    ring: 'ring-amber-400',
    dot: 'bg-amber-400',
  },
  {
    name: 'rose',
    badgeBg: 'bg-rose-950/80',
    badgeText: 'text-rose-300',
    badgeBorder: 'border-rose-700/60',
    quoteText: 'text-rose-100',
    quoteBg: 'bg-rose-950/20',
    ring: 'ring-rose-400',
    dot: 'bg-rose-400',
  },
  {
    name: 'violet',
    badgeBg: 'bg-violet-950/80',
    badgeText: 'text-violet-300',
    badgeBorder: 'border-violet-700/60',
    quoteText: 'text-violet-100',
    quoteBg: 'bg-violet-950/20',
    ring: 'ring-violet-400',
    dot: 'bg-violet-400',
  },
  {
    name: 'cyan',
    badgeBg: 'bg-cyan-950/80',
    badgeText: 'text-cyan-300',
    badgeBorder: 'border-cyan-700/60',
    quoteText: 'text-cyan-100',
    quoteBg: 'bg-cyan-950/20',
    ring: 'ring-cyan-400',
    dot: 'bg-cyan-400',
  },
  {
    name: 'lime',
    badgeBg: 'bg-lime-950/80',
    badgeText: 'text-lime-300',
    badgeBorder: 'border-lime-700/60',
    quoteText: 'text-lime-100',
    quoteBg: 'bg-lime-950/20',
    ring: 'ring-lime-400',
    dot: 'bg-lime-400',
  },
  {
    name: 'fuchsia',
    badgeBg: 'bg-fuchsia-950/80',
    badgeText: 'text-fuchsia-300',
    badgeBorder: 'border-fuchsia-700/60',
    quoteText: 'text-fuchsia-100',
    quoteBg: 'bg-fuchsia-950/20',
    ring: 'ring-fuchsia-400',
    dot: 'bg-fuchsia-400',
  },
  {
    name: 'orange',
    badgeBg: 'bg-orange-950/80',
    badgeText: 'text-orange-300',
    badgeBorder: 'border-orange-700/60',
    quoteText: 'text-orange-100',
    quoteBg: 'bg-orange-950/20',
    ring: 'ring-orange-400',
    dot: 'bg-orange-400',
  },
  {
    name: 'teal',
    badgeBg: 'bg-teal-950/80',
    badgeText: 'text-teal-300',
    badgeBorder: 'border-teal-700/60',
    quoteText: 'text-teal-100',
    quoteBg: 'bg-teal-950/20',
    ring: 'ring-teal-400',
    dot: 'bg-teal-400',
  },
  {
    name: 'indigo',
    badgeBg: 'bg-indigo-950/80',
    badgeText: 'text-indigo-300',
    badgeBorder: 'border-indigo-700/60',
    quoteText: 'text-indigo-100',
    quoteBg: 'bg-indigo-950/20',
    ring: 'ring-indigo-400',
    dot: 'bg-indigo-400',
  },
  {
    name: 'pink',
    badgeBg: 'bg-pink-950/80',
    badgeText: 'text-pink-300',
    badgeBorder: 'border-pink-700/60',
    quoteText: 'text-pink-100',
    quoteBg: 'bg-pink-950/20',
    ring: 'ring-pink-400',
    dot: 'bg-pink-400',
  },
];

/**
 * 32-bit FNV-1a hash of a string
 */
function fnv1a32(str: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    hash ^= str.charCodeAt(i);
    hash += (hash << 1) + (hash << 4) + (hash << 7) + (hash << 8) + (hash << 24);
  }
  return hash >>> 0;
}

export function getCharacterTheme(characterUuid: string, isNarrator = false): CharacterTheme {
  if (isNarrator || !characterUuid || characterUuid === 'narrator' || characterUuid.startsWith('char_narrator')) {
    return NARRATOR_THEME;
  }
  const hash = fnv1a32(characterUuid);
  const index = hash % PALETTE.length;
  return PALETTE[index];
}

