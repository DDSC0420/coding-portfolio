// Drives the real extension in a real, visible Chrome, the way a person would.
//
// Playwright loads the unpacked extension and controls ordinary tabs. Three things it can't do,
// because they belong to the browser rather than a page, are done here instead:
//   - keyboard shortcuts (Alt+Shift+H to save, Alt+Shift+M to open the popup) are pressed at the
//     X server with xdotool, so Chrome treats them as a person's key presses and grants activeTab;
//   - Chrome's own "add permissions" prompt is answered the same way (Tab, Enter);
//   - the real toolbar popup is reached through the DevTools protocol.
// So nothing is pre-granted: every permission the tests use was given the way a user gives it.
// Needs an X display (run under xvfb-run on a server) and xdotool.

import { chromium } from 'playwright';
import http from 'node:http';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const ROOT = fileURLToPath(new URL('../../', import.meta.url));
export const EXTENSION = join(ROOT, 'extension');
const FIXTURES = join(ROOT, 'test', 'fixtures');

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function until(fn, { timeout = 8000, interval = 100, message = 'condition' } = {}) {
  const deadline = Date.now() + timeout;
  let last;
  while (Date.now() < deadline) {
    try {
      last = await fn();
      if (last) return last;
    } catch (err) {
      last = err;
    }
    await sleep(interval);
  }
  throw new Error(`timed out waiting for ${message} (last: ${last instanceof Error ? last.message : JSON.stringify(last)})`);
}

// ---- fixture server: two origins (127.0.0.1 and localhost), pages swappable mid-test ----

export function startServer() {
  const routes = new Map([
    ['/article', 'article.html'],
    ['/late', 'late.html'],
  ]);
  const server = http.createServer((req, res) => {
    const path = new URL(req.url, 'http://x').pathname;
    if (path === '/favicon.ico') {
      res.writeHead(204).end();
      return;
    }
    const file = routes.get(path);
    if (!file) {
      res.writeHead(404).end('not found');
      return;
    }
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
    res.end(readFileSync(join(FIXTURES, file)));
  });
  return new Promise((resolve) => {
    server.listen(0, () => {
      const { port } = server.address();
      resolve({
        port,
        url: (path, host = '127.0.0.1') => `http://${host}:${port}${path}`,
        serve: (path, file) => routes.set(path, file),
        close: () => new Promise((r) => server.close(r)),
      });
    });
  });
}

// ---- the X server side ----

function xdo(...args) {
  return execFileSync('xdotool', args, { encoding: 'utf8' }).trim();
}

// The browser's top-level window (the biggest one with a title).
function browserWindow() {
  const ids = xdo('search', '--name', ' - (Google Chrome|Chromium)').split('\n').filter(Boolean);
  let best = null;
  for (const id of ids) {
    const geometry = xdo('getwindowgeometry', id);
    const [, w, h] = /Geometry: (\d+)x(\d+)/.exec(geometry) ?? [];
    const area = Number(w) * Number(h);
    if (!best || area > best.area) best = { id, area };
  }
  if (!best) throw new Error('browser window not found');
  return best.id;
}

/** Press keys as a person would, e.g. press('alt+shift+h'). */
export async function press(...keys) {
  xdo('windowfocus', '--sync', browserWindow());
  for (const key of keys) {
    xdo('key', '--clearmodifiers', key);
    await sleep(120);
  }
}

// ---- the browser ----

export async function launch() {
  const profile = mkdtempSync(join(tmpdir(), 'margin-profile-'));
  const options = {
    headless: false,
    viewport: null,
    args: [`--disable-extensions-except=${EXTENSION}`, `--load-extension=${EXTENSION}`, '--window-size=1280,900', '--window-position=0,0'],
  };
  if (process.env.CHROME_PATH) options.executablePath = process.env.CHROME_PATH;
  else if (process.env.PLAYWRIGHT_CHANNEL) options.channel = process.env.PLAYWRIGHT_CHANNEL;
  const context = await chromium.launchPersistentContext(profile, options);
  const worker = context.serviceWorkers()[0] ?? (await context.waitForEvent('serviceworker'));
  const id = new URL(worker.url()).host;
  const page = context.pages()[0] ?? (await context.newPage());

  const browser = {
    context,
    page,
    id,
    worker: () => context.serviceWorkers().find((w) => w.url().startsWith(`chrome-extension://${id}/`)) ?? worker,

    /** Run code in the extension's service worker. */
    sw(fn, arg) {
      return browser.worker().evaluate(fn, arg);
    },

    storage: () => browser.sw(() => chrome.storage.local.get(null)),
    pages: async () => {
      const all = await browser.storage();
      return Object.fromEntries(Object.entries(all).filter(([k]) => k.startsWith('page:')).map(([k, v]) => [k.slice(5), v]));
    },
    granted: () => browser.sw(() => chrome.permissions.getAll().then((p) => p.origins)),
    registered: () => browser.sw(() => chrome.scripting.getRegisteredContentScripts().then((s) => s.map((x) => x.matches))),
    badge: async () => {
      const tabId = await browser.sw(() => chrome.tabs.query({ active: true, lastFocusedWindow: true }).then(([t]) => t.id));
      return browser.sw((t) => chrome.action.getBadgeText({ tabId: t }), tabId);
    },

    /** What Margin has painted on the page, read from the page's own highlight registry. */
    painted: (target = page) =>
      target.evaluate(() => {
        const h = CSS.highlights.get('margin');
        if (!h) return null;
        return [...h].map((r) => r.toString().replace(/\s+/g, ' ').trim()).sort();
      }),

    /** Open the real toolbar popup with its keyboard shortcut, and get a handle on it. */
    async openPopup() {
      await page.bringToFront();
      await press('alt+shift+m');
      const cdp = await context.newCDPSession(page);
      const target = await until(
        async () => (await cdp.send('Target.getTargets')).targetInfos.find((t) => t.url === `chrome-extension://${id}/popup/popup.html`),
        { message: 'popup to open' },
      );
      const { sessionId } = await cdp.send('Target.attachToTarget', { targetId: target.targetId, flatten: false });
      let next = 0;
      const waiting = new Map();
      cdp.on('Target.receivedMessageFromTarget', (event) => {
        if (event.sessionId !== sessionId) return;
        const msg = JSON.parse(event.message);
        waiting.get(msg.id)?.(msg);
        waiting.delete(msg.id);
      });
      const call = (method, params = {}) =>
        new Promise((resolve, reject) => {
          const msgId = ++next;
          waiting.set(msgId, (msg) => (msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result)));
          cdp.send('Target.sendMessageToTarget', { sessionId, message: JSON.stringify({ id: msgId, method, params }) }).catch(reject);
        });
      const evaluate = async (expression, { gesture = false } = {}) => {
        const { result, exceptionDetails } = await call('Runtime.evaluate', {
          expression,
          returnByValue: true,
          awaitPromise: true,
          userGesture: gesture,
        });
        if (exceptionDetails) throw new Error(exceptionDetails.exception?.description ?? exceptionDetails.text);
        return result.value;
      };
      const popup = {
        evaluate,
        text: () => evaluate('document.body.innerText'),
        /** Click like a person: counts as a user gesture, so permission requests are allowed. */
        click: (selector) => evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`, { gesture: true }),
        rows: () =>
          evaluate(`[...document.querySelectorAll('.highlight')].map((li) => ({
            text: li.querySelector('blockquote').textContent,
            status: li.querySelector('.status').textContent,
          }))`),
        summary: () => evaluate(`document.getElementById('summary').textContent`),
        async close() {
          await evaluate('window.close()').catch(() => {});
          await cdp.detach().catch(() => {});
        },
        isOpen: async () => (await cdp.send('Target.getTargets')).targetInfos.some((t) => t.targetId === target.targetId),
        cdp,
      };
      await until(async () => !(await popup.summary()).startsWith('Looking'), { message: 'popup to load' });
      return popup;
    },

    /** Answer Chrome's own permission prompt. Deny is the default button; Allow is one Tab away. */
    async answerPermissionPrompt(allow) {
      await sleep(800);
      if (allow) await press('Tab', 'Return');
      else await press('Return');
    },

    async close() {
      await context.close();
      rmSync(profile, { recursive: true, force: true });
    },
  };
  return browser;
}

/** Select the n-th occurrence of some text on the page, across element boundaries, like dragging the mouse. */
export function selectText(page, text, nth = 0) {
  return page.evaluate(
    ([wanted, n]) => {
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      const nodes = [];
      let all = '';
      for (let t = walker.nextNode(); t; t = walker.nextNode()) {
        if (t.parentElement.closest('script, style')) continue;
        nodes.push([t, all.length]);
        all += t.data;
      }
      // Compare with whitespace collapsed, as a reader sees it; keep a map back to raw offsets.
      let flat = '';
      const map = [];
      for (let i = 0; i < all.length; i++) {
        if (/\s/.test(all[i]) && /\s$/.test(flat)) continue;
        flat += /\s/.test(all[i]) ? ' ' : all[i];
        map.push(i);
      }
      let at = -1;
      for (let i = 0; i <= n; i++) at = flat.indexOf(wanted, at + 1);
      if (at === -1) throw new Error(`"${wanted}" not on page`);
      const start = map[at];
      const end = map[at + wanted.length - 1] + 1;
      const point = (offset, isEnd) => {
        for (const [node, base] of nodes) {
          if (offset < base + node.data.length || (isEnd && offset === base + node.data.length)) return [node, offset - base];
        }
        throw new Error('offset past end');
      };
      const range = document.createRange();
      range.setStart(...point(start, false));
      range.setEnd(...point(end, true));
      const selection = getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      return selection.toString().replace(/\s+/g, ' ');
    },
    [text, nth],
  );
}

export function artifactsDir() {
  const dir = join(ROOT, 'test-results');
  mkdirSync(dir, { recursive: true });
  return dir;
}
