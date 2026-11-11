// The toolbar popup: this page's highlights, and the per-site "show automatically" switch.
// Opening the popup is a click on Margin, which gives it access to this one tab (activeTab);
// it uses that to put the content script on the page so the highlights appear.

import { originPattern, patternHost } from '../lib/urlkey.js';

const $ = (id) => document.getElementById(id);
const HINT_OFF = 'Off: Margin looks at this site only when you click it.';
const HINT_ON = 'On: Margin can read pages on this site, only to find your highlights. Turn off any time.';

let tab = null;
let rows = [];

function statusText(row) {
  if (!row.found) return ['Not found: the page has changed', 'lost'];
  if (row.method === 'exact') return ['On this page', 'ok'];
  return ['On this page · text changed slightly', 'changed'];
}

function summaryText() {
  if (rows.length === 0) return 'No highlights on this page yet.';
  const lost = rows.filter((r) => !r.found).length;
  const n = `${rows.length} highlight${rows.length === 1 ? '' : 's'}`;
  return lost ? `${n} · ${lost} not found` : n;
}

function render() {
  const list = $('list');
  list.replaceChildren();
  for (const row of rows) {
    const li = document.createElement('li');
    li.className = 'highlight';
    li.dataset.id = row.id;
    const quote = document.createElement('blockquote');
    quote.textContent = row.exact;
    const meta = document.createElement('div');
    meta.className = 'meta';
    const [text, kind] = statusText(row);
    const status = document.createElement('span');
    status.className = `status ${kind}`;
    status.textContent = text;
    meta.append(status);
    if (row.found) {
      const show = document.createElement('button');
      show.type = 'button';
      show.className = 'show';
      show.textContent = 'Show';
      show.addEventListener('click', () => chrome.tabs.sendMessage(tab.id, { type: 'scroll-to', id: row.id }, { frameId: 0 }));
      meta.append(show);
    }
    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'delete';
    del.textContent = 'Delete';
    del.setAttribute('aria-label', `Delete highlight: ${row.exact.slice(0, 60)}`);
    del.addEventListener('click', () => remove(row.id));
    meta.append(del);
    li.append(quote, meta);
    list.append(li);
  }
  $('summary').textContent = summaryText();
}

let pageKey = null;

async function remove(id) {
  const reply = await chrome.runtime.sendMessage({ type: 'delete', key: pageKey, id });
  if (reply?.ok) {
    rows = rows.filter((r) => r.id !== id);
    render();
  }
}

async function loadState() {
  const reply = await chrome.tabs.sendMessage(tab.id, { type: 'state' }, { frameId: 0 });
  if (!reply?.ok) throw new Error(reply?.error ?? 'no reply from page');
  pageKey = reply.result.key;
  rows = reply.result.highlights;
  render();
}

function cannotRun(why) {
  $('summary').textContent = 'Margin can’t run on this page.';
  $('site').textContent = why;
  $('tip').hidden = true;
  document.body.classList.add('unsupported');
}

async function setUpRemember(pattern) {
  const box = $('remember-box');
  const toggle = $('remember');
  $('host').textContent = patternHost(pattern);
  toggle.checked = await chrome.permissions.contains({ origins: [pattern] });
  $('remember-hint').textContent = toggle.checked ? HINT_ON : HINT_OFF;
  box.hidden = false;
  toggle.addEventListener('change', () => {
    // permissions.request must be called straight from the click, before any await.
    const asked = toggle.checked
      ? chrome.permissions.request({ origins: [pattern] })
      : chrome.permissions.remove({ origins: [pattern] }).then((removed) => !removed);
    asked.then(
      (on) => {
        toggle.checked = on;
        $('remember-hint').textContent = on ? HINT_ON : HINT_OFF;
      },
      () => {
        toggle.checked = !toggle.checked;
      },
    );
  });
}

async function showShortcut() {
  const commands = await chrome.commands.getAll();
  const save = commands.find((c) => c.name === 'save-highlight');
  $('shortcut').textContent = save?.shortcut || 'a shortcut you set in chrome://extensions/shortcuts';
}

async function main() {
  $('open-library').addEventListener('click', () => {
    chrome.tabs.create({ url: chrome.runtime.getURL('library/library.html') });
    window.close();
  });
  showShortcut();
  [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const pattern = originPattern(tab?.url ?? '');
  if (!tab || !pattern) {
    cannotRun('It works on web pages (http and https). Chrome keeps extensions off its own pages.');
    return;
  }
  const url = new URL(tab.url);
  $('site').textContent = `${url.host}${url.pathname === '/' ? '' : url.pathname}`;
  const injected = await chrome.runtime.sendMessage({ type: 'inject', tabId: tab.id });
  if (!injected?.ok) {
    cannotRun('Chrome doesn’t let extensions run here (for example the Chrome Web Store).');
    return;
  }
  await setUpRemember(pattern);
  await loadState();
  // Changes made elsewhere (the library page, another window) show here too.
  let timer = null;
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local' || !pageKey || !changes[`page:${pageKey}`]) return;
    clearTimeout(timer);
    timer = setTimeout(() => loadState().catch(() => {}), 150);
  });
}

main().catch((err) => {
  $('summary').textContent = `Something went wrong: ${err.message}`;
});
