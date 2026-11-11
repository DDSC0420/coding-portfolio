// Saved highlights, in chrome.storage.local. Only the service worker writes (through this module,
// one write at a time); the popup and the library page read.
//
// Layout: one entry per page, "page:<urlKey>" → { url, title, updatedAt, highlights: [...] }.
// A highlight is { id, exact, prefix, suffix, start, end, createdAt }.

import { urlKey } from './urlkey.js';

export const PAGE_PREFIX = 'page:';
export const LIMITS = Object.freeze({ exact: 5000, context: 64, title: 300 });

/** Check a selector that came from a content script. Returns a clean copy or throws. */
export function cleanSelector(sel) {
  if (sel === null || typeof sel !== 'object') throw new Error('selector missing');
  const { exact, prefix = '', suffix = '', start, end } = sel;
  if (typeof exact !== 'string' || !exact.trim()) throw new Error('selector has no text');
  if (exact.length > LIMITS.exact) throw new Error('selection too long');
  if (typeof prefix !== 'string' || typeof suffix !== 'string') throw new Error('bad context');
  if (prefix.length > LIMITS.context || suffix.length > LIMITS.context) throw new Error('context too long');
  if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 0 || end <= start) {
    throw new Error('bad position');
  }
  return { exact, prefix, suffix, start, end };
}

function cleanTitle(title, fallback) {
  const t = typeof title === 'string' ? title.replace(/\s+/g, ' ').trim() : '';
  return (t || fallback).slice(0, LIMITS.title);
}

/**
 * @param area   a chrome.storage area (or anything with the same get/set/remove promises)
 * @param opts   { now: () => Date, newId: () => string } for tests
 */
export function createStore(area, opts = {}) {
  const now = opts.now ?? (() => new Date());
  const newId = opts.newId ?? (() => crypto.randomUUID());
  let queue = Promise.resolve();

  // Run writes one after another so two saves can't both read the old page and lose one.
  function serial(fn) {
    const run = queue.then(fn, fn);
    queue = run.catch(() => {});
    return run;
  }

  async function getPage(key) {
    const storageKey = PAGE_PREFIX + key;
    const got = await area.get(storageKey);
    return got[storageKey] ?? null;
  }

  async function getAll() {
    const everything = await area.get(null);
    const pages = [];
    for (const [k, v] of Object.entries(everything)) {
      if (k.startsWith(PAGE_PREFIX) && v && Array.isArray(v.highlights)) {
        pages.push({ key: k.slice(PAGE_PREFIX.length), ...v });
      }
    }
    return pages;
  }

  /** Save a highlight for the page at pageUrl. The same passage saved twice is kept once. */
  function addHighlight(pageUrl, selector, { title } = {}) {
    return serial(async () => {
      const key = urlKey(pageUrl);
      if (!key) throw new Error('not a web page');
      const sel = cleanSelector(selector);
      const page = (await getPage(key)) ?? { url: key, title: '', highlights: [] };
      const same = page.highlights.find(
        (h) => h.exact === sel.exact && h.prefix === sel.prefix && h.suffix === sel.suffix,
      );
      if (same) return { key, id: same.id, duplicate: true };
      const stamp = now().toISOString();
      const highlight = { id: newId(), ...sel, createdAt: stamp };
      const next = {
        url: key,
        title: cleanTitle(title, page.title || key),
        updatedAt: stamp,
        highlights: [...page.highlights, highlight],
      };
      await area.set({ [PAGE_PREFIX + key]: next });
      return { key, id: highlight.id, duplicate: false };
    });
  }

  /** Remove one highlight; the page entry goes when its last highlight does. */
  function removeHighlight(key, id) {
    return serial(async () => {
      const page = await getPage(key);
      if (!page) return { removed: false };
      const highlights = page.highlights.filter((h) => h.id !== id);
      if (highlights.length === page.highlights.length) return { removed: false };
      if (highlights.length === 0) await area.remove(PAGE_PREFIX + key);
      else await area.set({ [PAGE_PREFIX + key]: { ...page, highlights, updatedAt: now().toISOString() } });
      return { removed: true };
    });
  }

  function forgetPage(key) {
    return serial(async () => {
      await area.remove(PAGE_PREFIX + key);
      return { removed: true };
    });
  }

  return { getPage, getAll, addHighlight, removeHighlight, forgetPage };
}
