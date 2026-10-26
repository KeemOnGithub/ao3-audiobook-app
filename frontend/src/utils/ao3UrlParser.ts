export const AO3_URL_REGEX =
  /^https?:\/\/(?:www\.|insecure\.)?archiveofourown\.org\/(?:collections\/[^/]+\/)?works\/(\d+)(?:\/chapters\/(\d+))?(?:[/?#].*)?$/i;

export interface ParsedAO3Url {
  isValid: boolean;
  workId: string | null;
  chapterId: string | null;
  canonicalUrl: string | null;
}

export function parseAO3Url(url: string): ParsedAO3Url {
  if (!url || typeof url !== 'string') {
    return { isValid: false, workId: null, chapterId: null, canonicalUrl: null };
  }

  const trimmed = url.trim();
  const match = trimmed.match(AO3_URL_REGEX);

  if (!match) {
    return { isValid: false, workId: null, chapterId: null, canonicalUrl: null };
  }

  const workId = match[1];
  const chapterId = match[2] || null;

  let canonicalUrl = `https://archiveofourown.org/works/${workId}`;
  if (chapterId) {
    canonicalUrl += `/chapters/${chapterId}`;
  }

  return {
    isValid: true,
    workId,
    chapterId,
    canonicalUrl,
  };
}

