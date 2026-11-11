// The whole extension in a real Chrome window, used the way a person uses it: keyboard
// shortcuts, the toolbar popup, Chrome's own permission prompt, reloads, a page that changes
// under its highlights, the library page and its export. One browser, one story, in order.
//
//   xvfb-run -a npm run test:browser      (or just npm run test:browser on a desktop)

import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { launch, startServer, selectText, press, until, sleep, artifactsDir } from './harness.js';

// The passages saved, and what a reader would call the same passages after the page is edited.
// Written from the fixture pages by hand.
const RULE = 'write down what you see, not what you expected to see'; // crosses <em> and <a>
const WICK = 'trimmed the wick'; // short: needs its context to agree
const NOON = "By noon the next day the harbour master had heard about the tables, and by one o'clock the whole quay knew.";
const WICK_EDITED = 'trimmed the lamp wick';
const NOON_EDITED = "By noon the next day the harbor master had heard about the tables, and by one o'clock the entire quay knew.";
const NOTICE = 'closed for repairs from Monday';

let server;
let b;
let page;

before(async () => {
  server = await startServer();
  b = await launch();
  page = b.page;
});

after(async () => {
  await b?.close();
  await server?.close();
});

// Each step screenshots the window if it fails, so CI shows what the page looked like.
function step(name, fn) {
  test(name, async () => {
    try {
      await fn();
    } catch (err) {
      const file = join(artifactsDir(), `${name.replace(/[^a-z0-9]+/gi, '-').slice(0, 60)}.png`);
      await page.screenshot({ path: file }).catch(() => {});
      throw err;
    }
  });
}

const sorted = (...texts) => [...texts].sort();
const article = () => server.url('/article');

async function save(text, nth = 0) {
  assert.equal(await selectText(page, text, nth), text);
  await press('alt+shift+h');
}

async function savedOnArticle() {
  return (await b.pages())[article()]?.highlights ?? [];
}

step('saves the selection with the real keyboard shortcut, even across links and emphasis', async () => {
  await page.goto(article());
  await save(RULE);
  await until(async () => (await savedOnArticle()).length === 1, { message: 'first highlight stored' });
  assert.equal(await page.locator('#margin-toast').getByText('Highlighted').count(), 1);
  const [stored] = await savedOnArticle();
  assert.equal(stored.exact, RULE);
  assert.equal(stored.prefix, ". The keeper's rule was simple: ");
  assert.equal(stored.suffix, '. Every keeper before her had co');
  assert.deepEqual(await until(() => b.painted()), [RULE]);
  assert.equal(await page.evaluate(() => getSelection().toString()), '', 'selection is cleared so the highlight shows');

  await save(WICK);
  await until(async () => (await savedOnArticle()).length === 2, { message: 'second highlight' });
  await save(NOON);
  await until(async () => (await savedOnArticle()).length === 3, { message: 'third highlight' });
  assert.deepEqual(await until(async () => ((await b.painted()).length === 3 ? b.painted() : null)), sorted(RULE, WICK, NOON));
  assert.equal(await b.badge(), '3');
  // Registered is not the same as visible: check the screen really shows yellow behind each one.
  for (const share of await b.paintedPixels()) assert.ok(share > 0.4, `highlight not visibly painted (${share.toFixed(2)} yellow)`);
});

step('the page itself never sees Margin: nothing added to its DOM, no way to message it', async () => {
  const report = await page.evaluate(() => ({
    marks: document.querySelectorAll('mark, [data-margin], [class*="margin"]').length,
    runtime: typeof globalThis.chrome?.runtime?.sendMessage,
  }));
  assert.deepEqual(report, { marks: 0, runtime: 'undefined' });
});

step('the shortcut with nothing selected says so and saves nothing', async () => {
  await page.evaluate(() => getSelection().removeAllRanges());
  await press('alt+shift+h');
  await until(() => page.locator('#margin-toast').getByText('Select some text first').count(), { message: 'toast' });
  assert.equal((await savedOnArticle()).length, 3);
});

step('the same passage saved twice is kept once', async () => {
  await save(WICK);
  await until(() => page.locator('#margin-toast').getByText('Already highlighted').count(), { message: 'toast' });
  assert.equal((await savedOnArticle()).length, 3);
});

step('by default Margin does not look at the page again: a reload shows nothing', async () => {
  await page.reload();
  await sleep(1500);
  assert.equal(await b.painted(), null, 'the content script did not run');
  assert.deepEqual(await b.granted(), []);
  assert.deepEqual(await b.registered(), []);
});

step('opening the popup (Alt+Shift+M) shows this page’s highlights, in page order', async () => {
  const popup = await b.openPopup();
  assert.equal(await popup.summary(), '3 highlights');
  assert.deepEqual(await popup.rows(), [
    { text: RULE, status: 'On this page' },
    { text: WICK, status: 'On this page' },
    { text: NOON, status: 'On this page' },
  ]);
  assert.deepEqual(await b.painted(), sorted(RULE, WICK, NOON));
  for (const share of await b.paintedPixels()) assert.ok(share > 0.4, `highlight not visibly painted (${share.toFixed(2)} yellow)`);
  await popup.close();
});

step('“show automatically” goes through Chrome’s own prompt; denying it changes nothing', async () => {
  const popup = await b.openPopup();
  assert.equal(await popup.evaluate(`document.getElementById('remember').checked`), false);
  await popup.click('#remember');
  await b.answerPermissionPrompt(false);
  await until(async () => (await popup.evaluate(`document.getElementById('remember').checked`)) === false, { message: 'switch back off' });
  assert.deepEqual(await b.granted(), []);
  assert.deepEqual(await b.registered(), []);
  await popup.close();
});

step('allowing it: the highlights come back on their own after a reload, on that site only', async () => {
  const popup = await b.openPopup();
  await popup.click('#remember');
  await b.answerPermissionPrompt(true);
  await until(async () => (await b.granted()).includes('http://127.0.0.1/*'), { message: 'permission granted' });
  assert.deepEqual(await b.granted(), ['http://127.0.0.1/*'], 'just this site, not every site');
  await until(async () => JSON.stringify(await b.registered()) === '[["http://127.0.0.1/*"]]', { message: 'content script registered' });
  assert.match(await popup.text(), /On: Margin can read pages on this site/);
  await popup.close();

  await page.reload();
  assert.deepEqual(await until(async () => ((await b.painted())?.length === 3 ? b.painted() : null)), sorted(RULE, WICK, NOON));
  assert.equal(await b.badge(), '3');
  for (const share of await b.paintedPixels()) assert.ok(share > 0.4, `highlight not visibly painted (${share.toFixed(2)} yellow)`);

  // Another site (localhost is a different origin) is still untouched.
  await page.goto(server.url('/article', 'localhost'));
  await sleep(1500);
  assert.equal(await b.painted(), null);
  await page.goto(article());
});

step('page edited a little: all three found, the edited two flagged as changed', async () => {
  server.serve('/article', 'article-edited.html');
  await page.reload();
  assert.equal(await page.title(), "The keeper's log (updated)");
  assert.deepEqual(
    await until(async () => ((await b.painted())?.length === 3 ? b.painted() : null)),
    sorted(RULE, WICK_EDITED, NOON_EDITED),
  );
  const popup = await b.openPopup();
  assert.deepEqual(await popup.rows(), [
    { text: RULE, status: 'On this page' },
    { text: WICK, status: 'On this page · text changed slightly' },
    { text: NOON, status: 'On this page · text changed slightly' },
  ]);
  await popup.close();
});

step('a passage rewritten: reported as not found, and nothing else highlighted in its place', async () => {
  server.serve('/article', 'article-rewritten.html');
  await page.reload();
  await until(async () => (await b.painted())?.length === 2, { message: 'two painted' });
  await sleep(500);
  assert.deepEqual(await b.painted(), sorted(RULE, WICK));
  assert.equal(await b.badge(), '2');
  const popup = await b.openPopup();
  assert.equal(await popup.summary(), '3 highlights · 1 not found');
  assert.deepEqual((await popup.rows())[2], { text: NOON, status: 'Not found: the page has changed' });
  await popup.close();
  server.serve('/article', 'article.html');
  await page.reload();
  await until(async () => (await b.painted())?.length === 3, { message: 'back to three' });
});

step('content that arrives after the page loads is highlighted when it arrives', async () => {
  await page.goto(server.url('/late'));
  await page.getByText(NOTICE, { exact: false }).waitFor();
  await save(NOTICE);
  await until(async () => (await b.pages())[server.url('/late')]?.highlights.length === 1, { message: 'saved' });
  await page.goto(server.url('/late#delay=1500'));
  await page.reload(); // same page (a plain #fragment is ignored), now slow to fill in
  await sleep(600);
  assert.deepEqual(await b.painted(), [], 'not there yet: Margin is running and waiting');
  assert.deepEqual(await until(async () => ((await b.painted())?.length ? b.painted() : null), { timeout: 6000 }), [NOTICE]);
});

step('“Show” in the popup scrolls to the highlight and flashes it', async () => {
  await page.goto(article());
  await until(async () => (await b.painted())?.length === 3, { message: 'painted' });
  await page.setViewportSize({ width: 1000, height: 300 });
  await page.evaluate(() => scrollTo(0, 0));
  const popup = await b.openPopup();
  await popup.evaluate(`document.querySelectorAll('.highlight')[2].querySelector('.show').click()`);
  await until(() => page.evaluate(() => CSS.highlights.get('margin-flash')?.size === 1), { message: 'flash' });
  await until(() => page.evaluate(() => scrollY > 200), { message: 'scrolled down to the third highlight' });
  await popup.close();
  await page.setViewportSize(null).catch(() => {});
});

step('the library lists everything, searches it, and exports Markdown', async () => {
  const library = await b.context.newPage();
  await library.goto(`chrome-extension://${b.id}/library/library.html`);
  await library.getByText('4 highlights on 2 pages').waitFor();
  assert.deepEqual(await library.locator('.page h2').allTextContents(), ['Loads in stages', "The keeper's log"], 'newest first');
  assert.deepEqual(await library.locator('#sites li span').allTextContents(), ['127.0.0.1']);

  await library.locator('#q').fill('quay noon');
  await library.getByText('1 highlight on 1 page match “quay noon” (of 4)').waitFor();
  assert.deepEqual(await library.locator('.highlight blockquote').allTextContents(), [NOON]);

  await library.locator('#q').fill('');
  const [download] = await Promise.all([library.waitForEvent('download'), library.locator('#export').click()]);
  const today = new Date().toISOString().slice(0, 10);
  assert.equal(download.suggestedFilename(), `margin-highlights-${today}.md`);
  const markdown = readFileSync(await download.path(), 'utf8');
  assert.equal(
    markdown,
    [
      '# Margin highlights',
      '',
      `Exported ${today} · 2 pages · 4 highlights`,
      '',
      `## [Loads in stages](${server.url('/late')})`,
      '',
      `> ${NOTICE}`,
      '',
      `## [The keeper's log](${article()})`,
      '',
      `> ${RULE}`,
      '',
      `> ${WICK}`,
      '',
      `> ${NOON}`,
      '',
    ].join('\n'),
  );
  await library.close();
});

step('a content script cannot read storage, delete highlights, or save them under another page', async () => {
  await page.bringToFront();
  const [first] = await savedOnArticle();
  const replies = await b.sw(async ({ key, id }) => {
    const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    // Runs in the page's isolated world, i.e. exactly where Margin's content script runs.
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      args: [key, id],
      func: async (k, i) => ({
        storage: await chrome.storage.local.get(null).then(() => 'read', (e) => e.message),
        del: await chrome.runtime.sendMessage({ type: 'delete', key: k, id: i }),
        save: await chrome.runtime.sendMessage({
          type: 'save',
          url: 'https://elsewhere.example/',
          selector: { exact: 'planted', prefix: '', suffix: '', start: 0, end: 7 },
        }),
      }),
    });
    return result;
  }, { key: article(), id: first.id });
  assert.equal(replies.storage, 'Access to storage is not allowed from this context.');
  assert.deepEqual(replies.del, { ok: false, error: 'not allowed' });
  assert.equal(replies.save.ok, true);
  assert.equal(replies.save.result.key, article(), 'saved under the page it came from, whatever the message said');
  assert.equal((await b.pages())['https://elsewhere.example/'], undefined);
  // tidy up the planted one through the popup's route
  await b.sw(({ key, id }) => chrome.storage.local.get(`page:${key}`).then((got) => {
    const page = got[`page:${key}`];
    page.highlights = page.highlights.filter((h) => h.id !== id);
    return chrome.storage.local.set({ [`page:${key}`]: page });
  }), { key: article(), id: replies.save.result.id });
  assert.equal((await savedOnArticle()).length, 3);
});

step('deleting in the popup takes the highlight off the page at once', async () => {
  const popup = await b.openPopup();
  await popup.evaluate(`document.querySelectorAll('.highlight')[1].querySelector('.delete').click()`);
  await until(async () => (await popup.rows()).length === 2, { message: 'row removed' });
  assert.deepEqual(await until(async () => ((await b.painted()).length === 2 ? b.painted() : null)), sorted(RULE, NOON));
  assert.deepEqual((await savedOnArticle()).map((h) => h.exact), [RULE, NOON]);
  assert.equal(await b.badge(), '2');
  await popup.close();
});

step('deleting in the library updates an open page too', async () => {
  const library = await b.context.newPage();
  await library.goto(`chrome-extension://${b.id}/library/library.html?q=harbour master`);
  await library.getByText('1 highlight on 1 page match').waitFor();
  await library.locator('.highlight .delete').click();
  await library.getByText('Nothing matches “harbour master” (of 2 highlights)').waitFor();
  await library.close();
  assert.deepEqual(await until(async () => ((await b.painted()).length === 1 ? b.painted() : null)), [RULE]);
});

step('“Stop” in the library turns automatic showing off for that site', async () => {
  const library = await b.context.newPage();
  await library.goto(`chrome-extension://${b.id}/library/library.html`);
  await library.getByRole('button', { name: 'Stop showing highlights automatically on 127.0.0.1' }).click();
  await library.getByText('None. Margin only reads a page when you click it.').waitFor();
  await library.close();
  assert.deepEqual(await b.granted(), []);
  await until(async () => (await b.registered()).length === 0, { message: 'unregistered' });
  await page.bringToFront();
  await page.reload();
  await sleep(1500);
  assert.equal(await b.painted(), null);
  assert.equal((await savedOnArticle()).length, 1, 'the highlights themselves are kept');
});

step('on a page Chrome keeps extensions off, the popup says so', async () => {
  await page.goto('chrome://version');
  const popup = await b.openPopup();
  assert.equal(await popup.summary(), 'Margin can’t run on this page.');
  await popup.close();
});
