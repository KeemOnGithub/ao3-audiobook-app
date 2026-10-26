import { describe, it, expect } from 'vitest';
import { parseAO3Url, AO3_URL_REGEX } from '../utils/ao3UrlParser';

describe('ao3UrlParser', () => {
  it('correctly matches valid AO3 work URLs with regex', () => {
    expect(AO3_URL_REGEX.test('https://archiveofourown.org/works/12345678')).toBe(true);
    expect(AO3_URL_REGEX.test('http://archiveofourown.org/works/12345678/chapters/87654321')).toBe(true);
    expect(AO3_URL_REGEX.test('https://insecure.archiveofourown.org/works/99999')).toBe(true);
    expect(AO3_URL_REGEX.test('https://archiveofourown.org/collections/fanfest/works/12345678')).toBe(true);
    expect(AO3_URL_REGEX.test('https://archiveofourown.org/works/12345678?view_adult=true#main')).toBe(true);
  });

  it('rejects invalid or non-AO3 URLs', () => {
    expect(AO3_URL_REGEX.test('https://fanfiction.net/s/12345678')).toBe(false);
    expect(AO3_URL_REGEX.test('https://archiveofourown.org/users/someone')).toBe(false);
    expect(AO3_URL_REGEX.test('not a url')).toBe(false);
    expect(AO3_URL_REGEX.test('')).toBe(false);
  });

  it('parses work-only URL canonical output', () => {
    const res = parseAO3Url('https://archiveofourown.org/works/12345678?view_full_work=true');
    expect(res.isValid).toBe(true);
    expect(res.workId).toBe('12345678');
    expect(res.chapterId).toBeNull();
    expect(res.canonicalUrl).toBe('https://archiveofourown.org/works/12345678');
  });

  it('parses work and chapter URL canonical output', () => {
    const res = parseAO3Url('https://archiveofourown.org/collections/fest/works/12345678/chapters/888888?view_adult=true');
    expect(res.isValid).toBe(true);
    expect(res.workId).toBe('12345678');
    expect(res.chapterId).toBe('888888');
    expect(res.canonicalUrl).toBe('https://archiveofourown.org/works/12345678/chapters/888888');
  });

  it('handles empty or malformed inputs safely', () => {
    expect(parseAO3Url('').isValid).toBe(false);
    expect(parseAO3Url(null as any).isValid).toBe(false);
    expect(parseAO3Url('random string').isValid).toBe(false);
  });
});

