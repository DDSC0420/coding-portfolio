// The "All highlights" page: every saved highlight, searchable, exportable as Markdown, and the
// list of sites Margin may read automatically. Page text is only ever inserted as text.

import { createStore } from '../lib/store.js';
import { filterPages, countHighlights, toMarkdown } from '../lib/library.js';
import { patternHost } from '../lib/urlkey.js';

const store = createStore(chrome.storage.local);
const $ = (id) => document.getElementById(id);
let allPages = [];

function plural(n, word) {
  return `${n} ${word}${n === 1 ? '' : 's'}`;
}

function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

function renderPages() {
  const query = $('q').value;
  const pages = filterPages(allPages, query);
  const total = countHighlights(allPages);
  const shown = countHighlights(pages);
  if (total === 0) {
    $('summary').textContent = 'No highlights yet. Select text on any page and press the shortcut, or right-click → Highlight with Margin.';
  } else if (query.trim() && shown === 0) {
    $('summary').textContent = `Nothing matches “${query.trim()}” (of ${plural(total, 'highlight')})`;
  } else if (query.trim()) {
    $('summary').textContent = `${plural(shown, 'highlight')} on ${plural(pages.length, 'page')} match “${query.trim()}” (of ${total})`;
  } else {
    $('summary').textContent = `${plural(total, 'highlight')} on ${plural(pages.length, 'page')}`;
  }
  $('export').disabled = shown === 0;

  const out = [];
  for (const page of pages) {
    const list = el('ol', { className: 'highlights' });
    for (const h of page.highlights) {
      const del = el('button', { type: 'button', className: 'delete', textContent: 'Delete' });
      del.setAttribute('aria-label', `Delete highlight: ${h.exact.slice(0, 60)}`);
      del.addEventListener('click', () => chrome.runtime.sendMessage({ type: 'delete', key: page.key, id: h.id }));
      const when = el('time', { dateTime: h.createdAt, textContent: new Date(h.createdAt).toLocaleDateString() });
      list.append(
        el('li', { className: 'highlight' },
          el('blockquote', {}, el('span', { className: 'mark', textContent: h.exact })),
          el('div', { className: 'meta' }, when, del)),
      );
    }
    const link = el('a', { href: page.url, target: '_blank', rel: 'noopener noreferrer', textContent: page.title || page.url });
    const forget = el('button', { type: 'button', className: 'link forget', textContent: 'Delete all on this page' });
    forget.addEventListener('click', () => {
      if (confirm(`Delete all ${plural(page.highlights.length, 'highlight')} on “${page.title || page.url}”?`)) {
        chrome.runtime.sendMessage({ type: 'forget-page', key: page.key });
      }
    });
    out.push(
      el('article', { className: 'page' },
        el('h2', {}, link),
        el('p', { className: 'url', textContent: page.url }),
        list,
        forget),
    );
  }
  $('pages').replaceChildren(...out);
}

async function renderSites() {
  const { origins = [] } = await chrome.permissions.getAll();
  const items = origins.map((pattern) => {
    const stop = el('button', { type: 'button', className: 'link', textContent: 'Stop' });
    stop.setAttribute('aria-label', `Stop showing highlights automatically on ${patternHost(pattern)}`);
    stop.addEventListener('click', () => chrome.permissions.remove({ origins: [pattern] }));
    return el('li', {}, el('span', { textContent: patternHost(pattern) }), stop);
  });
  if (items.length === 0) items.push(el('li', { className: 'none', textContent: 'None. Margin only reads a page when you click it.' }));
  $('sites').replaceChildren(...items);
}

function exportMarkdown() {
  const query = $('q').value;
  const pages = filterPages(allPages, query);
  const now = new Date();
  const blob = new Blob([toMarkdown(pages, { exportedAt: now, query })], { type: 'text/markdown' });
  const link = el('a', { href: URL.createObjectURL(blob), download: `margin-highlights-${now.toISOString().slice(0, 10)}.md` });
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 10000);
}

async function load() {
  allPages = await store.getAll();
  renderPages();
}

$('q').addEventListener('input', renderPages);
$('export').addEventListener('click', exportMarkdown);
chrome.storage.onChanged.addListener((_changes, area) => {
  if (area === 'local') load();
});
chrome.permissions.onAdded.addListener(renderSites);
chrome.permissions.onRemoved.addListener(renderSites);
const initial = new URLSearchParams(location.search).get('q');
if (initial) $('q').value = initial;
load();
renderSites();
