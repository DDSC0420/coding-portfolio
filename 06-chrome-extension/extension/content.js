// Margin's content script. Runs on a page only when the person acts (shortcut, right-click menu,
// toolbar button) or on a site they chose to remember. It turns the page into one string of text,
// asks lib/anchor.js where each saved passage is, and draws the results with the CSS Custom
// Highlight API, which paints ranges without adding or changing any of the page's elements.
//
// Loaded as a classic script after lib/anchor.js (content scripts can't be ES modules).

(() => {
  'use strict';

  if (globalThis.__marginContentLoaded) return; // injected twice (menu, then popup): keep the first
  globalThis.__marginContentLoaded = true;

  const A = globalThis.MarginAnchor;
  const SKIP = new Set(['script', 'style', 'noscript', 'template', 'textarea', 'select', 'iframe', 'object', 'canvas']);
  const WATCH_FOR_MS = 15000; // how long to keep retrying lost highlights while the page is still filling in

  const painted = new Highlight();
  const flash = new Highlight();
  flash.priority = 1;
  CSS.highlights.set('margin', painted);
  CSS.highlights.set('margin-flash', flash);

  let pageKey = null;
  let saved = []; // this page's highlights, as stored
  const placed = new Map(); // id → { range, method } when found, { lost: true } when not

  async function send(message) {
    try {
      return await chrome.runtime.sendMessage(message);
    } catch {
      return { ok: false, error: 'extension unavailable' }; // e.g. Margin was reloaded under this page
    }
  }

  // ---- the page as one string of text, and back ----

  function textIndex() {
    const nodes = [];
    const starts = [];
    const parts = [];
    const position = new Map();
    let length = 0;
    if (document.body) {
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
        acceptNode(n) {
          if (n.nodeType === Node.ELEMENT_NODE) {
            return SKIP.has(n.localName) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_SKIP;
          }
          return n.data.length ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
        },
      });
      for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        position.set(n, nodes.length);
        nodes.push(n);
        starts.push(length);
        parts.push(n.data);
        length += n.data.length;
      }
    }
    return { text: parts.join(''), nodes, starts, position, length };
  }

  // A DOM boundary point → a character offset in the index.
  function pointToOffset(idx, container, offset) {
    const i = idx.position.get(container);
    if (i !== undefined) return idx.starts[i] + Math.min(offset, container.data.length);
    // Not one of our text nodes (an element boundary, or text we skip): use the first text at or after it.
    const probe = document.createRange();
    probe.setStart(container, offset);
    let lo = 0;
    let hi = idx.nodes.length;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (probe.comparePoint(idx.nodes[mid], 0) < 0) lo = mid + 1;
      else hi = mid;
    }
    return lo < idx.nodes.length ? idx.starts[lo] : idx.length;
  }

  // Last node index whose start is <= offset (or < offset when strict).
  function nodeAt(idx, offset, strict) {
    let lo = 0;
    let hi = idx.starts.length;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (strict ? idx.starts[mid] < offset : idx.starts[mid] <= offset) lo = mid + 1;
      else hi = mid;
    }
    return Math.max(0, lo - 1);
  }

  // Character offsets → a live Range. A start on a node boundary goes to the next node, an end to the previous.
  function offsetsToRange(idx, start, end) {
    let s = nodeAt(idx, start, false);
    while (s < idx.nodes.length - 1 && start - idx.starts[s] >= idx.nodes[s].data.length) s++;
    const e = nodeAt(idx, end, true);
    const range = document.createRange();
    range.setStart(idx.nodes[s], Math.min(start - idx.starts[s], idx.nodes[s].data.length));
    range.setEnd(idx.nodes[e], Math.min(end - idx.starts[e], idx.nodes[e].data.length));
    return range;
  }

  // ---- drawing ----

  let watchUntil = 0;
  let observer = null;
  let retryTimer = null;

  function watchForLateContent() {
    watchUntil = Date.now() + WATCH_FOR_MS;
    if (observer || !document.body) return;
    observer = new MutationObserver(() => {
      if (Date.now() > watchUntil) {
        observer.disconnect();
        observer = null;
        return;
      }
      clearTimeout(retryTimer);
      retryTimer = setTimeout(() => render(saved, { keepWatching: false }), 300);
    });
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  }

  function render(list, { keepWatching = true } = {}) {
    saved = list;
    const ids = new Set(list.map((h) => h.id));
    for (const [id, p] of placed) {
      const broken = p.range && (p.range.collapsed || !p.range.startContainer.isConnected);
      if (!ids.has(id) || broken) {
        if (p.range) painted.delete(p.range);
        placed.delete(id);
      }
    }
    const todo = list.filter((h) => !placed.get(h.id)?.range);
    if (todo.length) {
      const idx = textIndex();
      for (const h of todo) {
        const hit = idx.nodes.length ? A.anchor(idx.text, h) : null;
        if (hit) {
          const range = offsetsToRange(idx, hit.start, hit.end);
          painted.add(range);
          placed.set(h.id, { range, method: hit.method });
        } else {
          placed.set(h.id, { lost: true });
        }
      }
    }
    const found = list.filter((h) => placed.get(h.id)?.range).length;
    send({ type: 'count', found, total: list.length });
    if (found < list.length && keepWatching) watchForLateContent();
  }

  async function refresh() {
    const reply = await send({ type: 'page-ready' });
    if (!reply?.ok) return;
    pageKey = reply.result.key;
    render(reply.result.highlights);
  }

  // ---- saving the selection ----

  function capture() {
    const selection = window.getSelection();
    if (!selection || selection.rangeCount === 0 || selection.isCollapsed) return { error: 'no-selection' };
    const range = selection.getRangeAt(0);
    const idx = textIndex();
    const start = pointToOffset(idx, range.startContainer, range.startOffset);
    const end = pointToOffset(idx, range.endContainer, range.endOffset);
    const selector = A.describe(idx.text, start, end);
    if (!selector) return { error: 'no-selection' }; // only whitespace, or inside a text box
    if (selector.error) return { error: selector.error };
    return { selector };
  }

  let toastTimer = null;
  function toast(text) {
    let host = document.getElementById('margin-toast');
    if (!host) {
      host = document.createElement('div');
      host.id = 'margin-toast';
      const root = host.attachShadow({ mode: 'open' });
      root.innerHTML = `<style>
        div { position: fixed; z-index: 2147483647; bottom: 24px; left: 50%; transform: translateX(-50%);
          font: 500 14px/1.3 system-ui, sans-serif; color: #fff; background: #23262e; padding: 10px 16px;
          border-radius: 8px; box-shadow: 0 6px 24px rgb(0 0 0 / 0.25); pointer-events: none; }
      </style><div role="status"></div>`;
      document.documentElement.append(host);
    }
    host.shadowRoot.querySelector('div').textContent = text;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => host.remove(), 2200);
  }

  async function saveSelection() {
    const got = capture();
    if (got.error) {
      toast(got.error === 'too-long' ? 'That’s too long to highlight (5,000 characters at most).' : 'Select some text first, then highlight it.');
      return { ok: false, error: got.error };
    }
    const reply = await send({ type: 'save', selector: got.selector });
    if (!reply?.ok) {
      toast('Margin couldn’t save that highlight.');
      return { ok: false, error: reply?.error };
    }
    window.getSelection()?.removeAllRanges();
    toast(reply.result.duplicate ? 'Already highlighted' : 'Highlighted');
    await refresh();
    return { ok: true, id: reply.result.id };
  }

  // ---- what the popup sees ----

  function state() {
    const rows = saved.map((h) => {
      const p = placed.get(h.id);
      return { id: h.id, exact: h.exact, createdAt: h.createdAt, found: Boolean(p?.range), method: p?.method ?? null, range: p?.range };
    });
    rows.sort((a, b) => {
      if (a.range && b.range) return a.range.compareBoundaryPoints(Range.START_TO_START, b.range);
      return a.range ? -1 : b.range ? 1 : 0;
    });
    return { key: pageKey, title: document.title, highlights: rows.map(({ range, ...row }) => row) };
  }

  function scrollTo(id) {
    const p = placed.get(id);
    if (!p?.range) return { ok: false };
    p.range.startContainer.parentElement?.scrollIntoView({ block: 'center', behavior: 'smooth' });
    flash.clear();
    flash.add(p.range);
    setTimeout(() => flash.clear(), 1600);
    return { ok: true };
  }

  const handlers = {
    ping: () => ({ ok: true }),
    capture: () => saveSelection(),
    state: async () => {
      await ready; // the popup may ask the moment this script lands
      return { ok: true, result: state() };
    },
    'scroll-to': (msg) => scrollTo(msg.id),
  };

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (sender.id !== chrome.runtime.id) return false;
    const handler = handlers[msg?.type];
    if (!handler) return false;
    Promise.resolve(handler(msg)).then(sendResponse, (err) => sendResponse({ ok: false, error: String(err) }));
    return true;
  });

  // Highlights deleted or added elsewhere (popup, library, another tab) show up here at once.
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local' || !pageKey) return;
    const change = changes[`page:${pageKey}`];
    if (change) render(change.newValue?.highlights ?? []);
  });

  const ready = refresh();
})();
