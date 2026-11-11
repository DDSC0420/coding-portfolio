// Margin's service worker: saving, the opt-in "remember this site" registration, the badge, and
// the one place highlights are written.
//
// Messages are checked by who sent them. A content script (running on a web page) may only ask
// for its own page's highlights, save to its own page, and report its count; the page's address
// comes from Chrome (sender.url), never from the message. Only Margin's own pages (popup,
// library) may delete. Web pages themselves can't message the extension at all.

import { urlKey } from './lib/urlkey.js';
import { createStore } from './lib/store.js';

const store = createStore(chrome.storage.local);
const MENU_ID = 'margin-save';
const SCRIPT_ID = 'margin-remembered-sites';
const CONTENT_JS = ['lib/anchor.js', 'content.js'];
const CONTENT_CSS = ['content.css'];
const EXTENSION_ORIGIN = chrome.runtime.getURL('');

// ---- remembered sites: run the content script automatically only where the person said so ----

async function syncRegistration() {
  const { origins = [] } = await chrome.permissions.getAll();
  const matches = origins.filter((o) => /^(https?|\*):\/\//.test(o) || o === '<all_urls>');
  const existing = await chrome.scripting.getRegisteredContentScripts({ ids: [SCRIPT_ID] });
  if (matches.length === 0) {
    if (existing.length) await chrome.scripting.unregisterContentScripts({ ids: [SCRIPT_ID] });
    return;
  }
  const script = { id: SCRIPT_ID, matches, js: CONTENT_JS, css: CONTENT_CSS, runAt: 'document_idle' };
  if (existing.length) await chrome.scripting.updateContentScripts([script]);
  else await chrome.scripting.registerContentScripts([script]);
}

// Keep the menu and the registration right however the worker was started.
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({ id: MENU_ID, title: 'Highlight with Margin', contexts: ['selection'] });
  });
  syncRegistration();
});
chrome.runtime.onStartup.addListener(syncRegistration);
chrome.permissions.onAdded.addListener(syncRegistration);
chrome.permissions.onRemoved.addListener(syncRegistration);

// ---- saving: right-click menu or keyboard shortcut, both of which grant activeTab ----

async function hasContentScript(tabId) {
  try {
    const reply = await chrome.tabs.sendMessage(tabId, { type: 'ping' }, { frameId: 0 });
    return reply?.ok === true;
  } catch {
    return false;
  }
}

/** Put the content script on a tab (no-op if it's there). Throws on pages extensions can't touch. */
async function ensureContentScript(tabId) {
  if (await hasContentScript(tabId)) return;
  await chrome.scripting.insertCSS({ target: { tabId }, files: CONTENT_CSS, origin: 'USER' });
  await chrome.scripting.executeScript({ target: { tabId }, files: CONTENT_JS });
}

async function saveSelection(tab) {
  if (!tab?.id) return;
  try {
    await ensureContentScript(tab.id);
  } catch {
    flashBadge(tab.id, '×', 'Margin can’t run on this page');
    return;
  }
  await chrome.tabs.sendMessage(tab.id, { type: 'capture' }, { frameId: 0 }).catch(() => {});
}

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === MENU_ID) saveSelection(tab);
});
chrome.commands.onCommand.addListener((command, tab) => {
  if (command === 'save-highlight') saveSelection(tab);
});

// ---- badge ----

function setCount(tabId, { found, total }) {
  if (!Number.isInteger(found) || !Number.isInteger(total)) return;
  const lost = total - found;
  chrome.action.setBadgeBackgroundColor({ tabId, color: lost ? '#9a6700' : '#3b3f4a' });
  chrome.action.setBadgeText({ tabId, text: total ? String(found) : '' });
  const title = total
    ? `Margin: ${found} highlight${found === 1 ? '' : 's'} on this page${lost ? ` (${lost} not found)` : ''}`
    : 'Margin';
  chrome.action.setTitle({ tabId, title });
}

function flashBadge(tabId, text, title) {
  chrome.action.setBadgeBackgroundColor({ tabId, color: '#b42318' });
  chrome.action.setBadgeText({ tabId, text });
  chrome.action.setTitle({ tabId, title });
  setTimeout(() => chrome.action.setBadgeText({ tabId, text: '' }).catch(() => {}), 2500);
}

// ---- messages ----

function fromExtensionPage(sender) {
  return typeof sender.url === 'string' && sender.url.startsWith(EXTENSION_ORIGIN);
}

async function handle(msg, sender) {
  if (!msg || typeof msg.type !== 'string') throw new Error('bad message');

  if (fromExtensionPage(sender)) {
    switch (msg.type) {
      case 'delete':
        return store.removeHighlight(String(msg.key), String(msg.id));
      case 'forget-page':
        return store.forgetPage(String(msg.key));
      case 'inject':
        await ensureContentScript(Number(msg.tabId));
        return { ok: true };
      default:
        throw new Error(`unknown message ${msg.type}`);
    }
  }

  // Everything else must be Margin's content script in the top frame of a tab.
  if (!sender.tab || sender.frameId !== 0) throw new Error('not allowed');
  const key = urlKey(sender.url);
  if (!key) throw new Error('not a web page');
  switch (msg.type) {
    case 'page-ready': {
      const page = await store.getPage(key);
      return { key, highlights: page?.highlights ?? [] };
    }
    case 'save':
      return store.addHighlight(sender.url, msg.selector, { title: sender.tab.title });
    case 'count':
      setCount(sender.tab.id, msg);
      return { ok: true };
    default:
      throw new Error('not allowed');
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (sender.id !== chrome.runtime.id) return false;
  handle(msg, sender).then(
    (result) => sendResponse({ ok: true, result }),
    (err) => sendResponse({ ok: false, error: String(err?.message ?? err) }),
  );
  return true; // reply asynchronously
});
