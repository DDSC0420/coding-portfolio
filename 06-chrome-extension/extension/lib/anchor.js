/*
 * Margin: describing a passage of page text, and finding it again later.
 *
 * Pure string functions, no DOM. The content script hands in the page's text as one string;
 * this module never sees an element. Loaded as a classic script (content scripts can't be
 * ES modules), so it publishes itself on globalThis.MarginAnchor; the unit tests import it
 * the same way.
 *
 * A saved passage is a W3C Web Annotation style pair of selectors:
 *   TextQuoteSelector    { exact, prefix, suffix }  the words, with up to 32 characters either side
 *   TextPositionSelector { start, end }             where they were, used only as a tie-break hint
 * Both are measured in whitespace-normalised text, so re-flowed lines or a &nbsp; don't matter.
 *
 * Finding it again, in order:
 *   1. exact    the quote occurs as-is; with several copies, context and position pick one
 *   2. fuzzy    a close match (at most a quarter of the characters edited), checked against context
 *   3. context  the words either side are intact and the gap between them is at least 60% similar
 * Otherwise it is reported lost. A wrong highlight is worse than none, so a short quote
 * (under 20 characters) needs its surrounding context to agree before it is accepted.
 */
(function () {
  'use strict';

  const CONTEXT = 32; // characters of prefix and suffix kept
  const MAX_QUOTE = 5000; // longest passage that can be saved
  const SHORT_QUOTE = 20; // below this, an exact match also needs its context to agree
  const FUZZY_RATE = 0.25; // fuzzy: at most this share of the quote's characters edited
  const DIRECT_FUZZY = 64; // longer quotes are fuzzy-matched by their first and last 32 characters
  const EDGE = 32;
  const MAX_CANDIDATES = 500;
  const CONTEXT_MIN_SIM = 0.6; // context stage: how similar the bracketed text must still be
  const WEIGHTS = { quote: 50, prefix: 20, suffix: 20, position: 2 };

  function isSpace(code) {
    // ASCII whitespace, NBSP, the Unicode space separators, line/paragraph separators, BOM
    return (
      code === 32 || (code >= 9 && code <= 13) || code === 160 || code === 0x1680 ||
      (code >= 0x2000 && code <= 0x200a) || code === 0x2028 || code === 0x2029 ||
      code === 0x202f || code === 0x205f || code === 0x3000 || code === 0xfeff
    );
  }

  /**
   * Collapse every run of whitespace to a single space.
   * map[i] is the raw index of normalised character i; map[text.length] is raw.length.
   */
  function normalize(raw) {
    const out = [];
    const map = [];
    let inSpace = false;
    for (let i = 0; i < raw.length; i++) {
      const code = raw.charCodeAt(i);
      if (isSpace(code)) {
        if (inSpace) continue;
        inSpace = true;
        out.push(' ');
      } else {
        inSpace = false;
        out.push(raw[i]);
      }
      map.push(i);
    }
    map.push(raw.length);
    return { text: out.join(''), map };
  }

  // First index k with sorted[k] >= value.
  function lowerBound(sorted, value) {
    let lo = 0;
    let hi = sorted.length;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (sorted[mid] < value) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  }

  /**
   * Describe raw[rawStart, rawEnd) as a selector.
   * Returns null if the range holds no visible characters, { error: 'too-long' } past MAX_QUOTE.
   */
  function describe(raw, rawStart, rawEnd) {
    if (!(rawStart >= 0 && rawEnd <= raw.length && rawStart < rawEnd)) return null;
    const { text, map } = normalize(raw);
    let start = lowerBound(map, rawStart);
    let end = lowerBound(map, rawEnd);
    while (start < end && text[start] === ' ') start++;
    while (end > start && text[end - 1] === ' ') end--;
    if (start === end) return null;
    if (end - start > MAX_QUOTE) return { error: 'too-long' };
    return {
      exact: text.slice(start, end),
      prefix: text.slice(Math.max(0, start - CONTEXT), start),
      suffix: text.slice(end, end + CONTEXT),
      start,
      end,
    };
  }

  /**
   * Edit distance (insert, delete, substitute) between a and b.
   * With a limit, gives up early and returns limit + 1 once the distance must exceed it.
   */
  function editDistance(a, b, limit) {
    if (a === b) return 0;
    const n = a.length;
    const m = b.length;
    const cap = Math.min(limit === undefined ? Infinity : limit, Math.max(n, m));
    if (Math.abs(n - m) > cap) return cap + 1;
    if (n === 0) return m;
    if (m === 0) return n;
    let prev = new Int32Array(m + 1);
    let cur = new Int32Array(m + 1);
    for (let j = 0; j <= m; j++) prev[j] = j;
    for (let i = 1; i <= n; i++) {
      const lo = Math.max(1, i - cap);
      const hi = Math.min(m, i + cap);
      cur[0] = i;
      if (lo > 1) cur[lo - 1] = cap + 1;
      let rowMin = lo > 1 ? cap + 1 : i;
      const ca = a.charCodeAt(i - 1);
      for (let j = lo; j <= hi; j++) {
        let v = prev[j - 1] + (ca === b.charCodeAt(j - 1) ? 0 : 1);
        const del = prev[j] + 1;
        if (del < v) v = del;
        const ins = cur[j - 1] + 1;
        if (ins < v) v = ins;
        cur[j] = v;
        if (v < rowMin) rowMin = v;
      }
      if (hi < m) cur[hi + 1] = cap + 1;
      if (rowMin > cap) return cap + 1;
      const swap = prev;
      prev = cur;
      cur = swap;
    }
    return Math.min(prev[m], cap + 1);
  }

  function similarity(a, b) {
    const longest = Math.max(a.length, b.length);
    if (longest === 0) return 1;
    return 1 - editDistance(a, b) / longest;
  }

  /**
   * Approximate substring search (Sellers): every place where some substring of text is within
   * maxErrors edits of pattern. Overlapping hits are merged, keeping the one with fewest errors.
   * O(pattern.length × text.length), so callers keep the pattern short.
   */
  function approxSearch(text, pattern, maxErrors) {
    const m = pattern.length;
    const cost = new Int32Array(m + 1);
    const from = new Int32Array(m + 1);
    for (let i = 0; i <= m; i++) cost[i] = i;
    const hits = [];
    for (let j = 1; j <= text.length; j++) {
      const c = text.charCodeAt(j - 1);
      let diag = cost[0];
      let diagFrom = from[0];
      cost[0] = 0;
      from[0] = j;
      for (let i = 1; i <= m; i++) {
        const up = cost[i];
        const upFrom = from[i];
        let v = diag + (pattern.charCodeAt(i - 1) === c ? 0 : 1);
        let vFrom = diagFrom;
        if (up + 1 < v) {
          v = up + 1;
          vFrom = upFrom;
        }
        if (cost[i - 1] + 1 < v) {
          v = cost[i - 1] + 1;
          vFrom = from[i - 1];
        }
        diag = up;
        diagFrom = upFrom;
        cost[i] = v;
        from[i] = vFrom;
      }
      if (cost[m] <= maxErrors) {
        const hit = { start: from[m], end: j, errors: cost[m] };
        const last = hits[hits.length - 1];
        if (last && hit.start < last.end) {
          if (hit.errors < last.errors) hits[hits.length - 1] = hit;
        } else {
          if (hits.length >= MAX_CANDIDATES) break;
          hits.push(hit);
        }
      }
    }
    return hits;
  }

  function indexesOf(text, needle) {
    const found = [];
    let at = text.indexOf(needle);
    while (at !== -1 && found.length < MAX_CANDIDATES) {
      found.push(at);
      at = text.indexOf(needle, at + 1);
    }
    return found;
  }

  // How well the text around [start, end) agrees with the saved prefix and suffix (0..1 each).
  function contextAgreement(text, start, end, sel) {
    const prefix = sel.prefix || '';
    const suffix = sel.suffix || '';
    const before = text.slice(Math.max(0, start - prefix.length), start);
    const after = text.slice(end, end + suffix.length);
    // An empty saved prefix means the quote began the page: still true scores 1, otherwise neutral.
    const prefixSim = prefix ? similarity(prefix, before) : start === 0 ? 1 : 0.5;
    const suffixSim = suffix ? similarity(suffix, after) : end === text.length ? 1 : 0.5;
    return { prefixSim, suffixSim };
  }

  function score(text, start, end, quoteSim, sel) {
    const { prefixSim, suffixSim } = contextAgreement(text, start, end, sel);
    let position = 0;
    if (Number.isInteger(sel.start) && text.length) {
      position = 1 - Math.min(1, Math.abs(start - sel.start) / text.length);
    }
    const total =
      WEIGHTS.quote * quoteSim + WEIGHTS.prefix * prefixSim + WEIGHTS.suffix * suffixSim +
      WEIGHTS.position * position;
    return { start, end, quoteSim, prefixSim, suffixSim, total };
  }

  function best(candidates) {
    let top = null;
    for (const c of candidates) if (!top || c.total > top.total) top = c;
    return top;
  }

  function supported(c) {
    return Math.max(c.prefixSim, c.suffixSim) >= 0.5;
  }

  function trimEnds(text, start, end) {
    while (start < end && text[start] === ' ') start++;
    while (end > start && text[end - 1] === ' ') end--;
    return [start, end];
  }

  // Candidate spans for a quote too long to fuzzy-match directly: pair up near-matches of its
  // first and last 32 characters whose distance apart is within a quarter of the quote's length.
  function longCandidates(text, exact) {
    const m = exact.length;
    const allowed = Math.floor(EDGE * FUZZY_RATE);
    const heads = approxSearch(text, exact.slice(0, EDGE), allowed);
    const tails = approxSearch(text, exact.slice(-EDGE), allowed);
    const spans = [];
    for (const h of heads) {
      for (const t of tails) {
        const length = t.end - h.start;
        if (length < m * (1 - FUZZY_RATE) || length > m * (1 + FUZZY_RATE)) continue;
        spans.push({ start: h.start, end: t.end, edgeErrors: h.errors + t.errors });
      }
    }
    spans.sort((a, b) => a.edgeErrors - b.edgeErrors);
    return spans.slice(0, 5);
  }

  /** Find a selector in normalised text. Returns { start, end, method, ...scores } or null. */
  function locate(text, sel) {
    const exact = sel.exact;
    const m = exact.length;

    // 1. exact
    const exactHits = indexesOf(text, exact).map((at) => score(text, at, at + m, 1, sel));
    const exactBest = best(m >= SHORT_QUOTE ? exactHits : exactHits.filter(supported));
    if (exactBest) return { ...exactBest, method: 'exact' };

    // 2. fuzzy
    const allowed = Math.floor(m * FUZZY_RATE);
    if (allowed >= 1) {
      let spans;
      if (m <= DIRECT_FUZZY) {
        spans = approxSearch(text, exact, allowed);
      } else {
        spans = longCandidates(text, exact);
      }
      const fuzzy = [];
      for (const span of spans) {
        const [start, end] = trimEnds(text, span.start, span.end);
        if (start === end) continue;
        const errors = editDistance(exact, text.slice(start, end), allowed);
        if (errors > allowed) continue;
        const c = score(text, start, end, 1 - errors / m, sel);
        if (m >= SHORT_QUOTE || supported(c)) fuzzy.push(c);
      }
      const fuzzyBest = best(fuzzy);
      if (fuzzyBest) return { ...fuzzyBest, method: 'fuzzy' };
    }

    // 3. context: the words either side survive, the passage between them changed more than fuzzy allows
    const before = (sel.prefix || '').slice(-16);
    const after = (sel.suffix || '').slice(0, 16);
    if (before.length >= 8 && after.length >= 8) {
      const bracketed = [];
      for (const p of indexesOf(text, before)) {
        const gapStart = p + before.length;
        const s = text.indexOf(after, gapStart);
        if (s === -1) continue;
        const length = s - gapStart;
        if (length < m * 0.5 || length > m * 1.5) continue;
        const [start, end] = trimEnds(text, gapStart, s);
        if (start === end) continue;
        const quoteSim = similarity(exact, text.slice(start, end));
        if (quoteSim < CONTEXT_MIN_SIM) continue;
        bracketed.push(score(text, start, end, quoteSim, sel));
      }
      const contextBest = best(bracketed);
      if (contextBest) return { ...contextBest, method: 'context' };
    }
    return null;
  }

  function isSelector(sel) {
    return (
      sel !== null && typeof sel === 'object' && typeof sel.exact === 'string' &&
      sel.exact.length > 0 && sel.exact.length <= MAX_QUOTE &&
      (sel.prefix === undefined || typeof sel.prefix === 'string') &&
      (sel.suffix === undefined || typeof sel.suffix === 'string')
    );
  }

  /**
   * Find a saved selector in the page's raw text.
   * Returns { start, end, method } in raw indexes, or null if it can't be found with confidence.
   */
  function anchor(raw, sel) {
    if (!isSelector(sel)) return null;
    const { text, map } = normalize(raw);
    const found = locate(text, sel);
    if (!found) return null;
    return {
      start: map[found.start],
      end: map[found.end - 1] + 1,
      method: found.method,
      quoteSim: found.quoteSim,
    };
  }

  globalThis.MarginAnchor = Object.freeze({
    CONTEXT,
    MAX_QUOTE,
    normalize,
    describe,
    anchor,
    locate,
    editDistance,
    similarity,
    approxSearch,
  });
})();
