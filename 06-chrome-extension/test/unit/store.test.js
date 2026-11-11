import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createStore, cleanSelector } from '../../extension/lib/store.js';

// The same promise interface as chrome.storage.local, in memory. Each call yields to the event
// loop first, so overlapping writes interleave the way they can in the real thing.
function memoryArea() {
  const data = new Map();
  const tick = () => new Promise((r) => setTimeout(r, 0));
  return {
    data,
    async get(key) {
      await tick();
      if (key === null) return Object.fromEntries(structuredClone([...data]));
      return data.has(key) ? { [key]: structuredClone(data.get(key)) } : {};
    },
    async set(items) {
      await tick();
      for (const [k, v] of Object.entries(items)) data.set(k, structuredClone(v));
    },
    async remove(key) {
      await tick();
      data.delete(key);
    },
  };
}

function setup() {
  const area = memoryArea();
  let n = 0;
  const store = createStore(area, { now: () => new Date('2026-11-11T09:30:00Z'), newId: () => `h${++n}` });
  return { area, store };
}

const SEL = { exact: 'the tide tables were wrong', prefix: 'On Tuesday ', suffix: ' again.', start: 11, end: 37 };

test('a saved highlight is stored under its page, with a timestamp', async () => {
  const { store } = setup();
  const saved = await store.addHighlight('https://example.com/log?utm_source=x#today', SEL, { title: '  The   log ' });
  assert.deepEqual(saved, { key: 'https://example.com/log', id: 'h1', duplicate: false });
  assert.deepEqual(await store.getPage('https://example.com/log'), {
    url: 'https://example.com/log',
    title: 'The log',
    updatedAt: '2026-11-11T09:30:00.000Z',
    highlights: [{ id: 'h1', ...SEL, createdAt: '2026-11-11T09:30:00.000Z' }],
  });
});

test('saving the same passage twice keeps one copy', async () => {
  const { store } = setup();
  await store.addHighlight('https://example.com/a', SEL);
  const again = await store.addHighlight('https://example.com/a', { ...SEL, start: 99, end: 125 });
  assert.equal(again.duplicate, true);
  assert.equal(again.id, 'h1');
  assert.equal((await store.getPage('https://example.com/a')).highlights.length, 1);
});

test('twenty saves at once are all kept (writes are queued, not raced)', async () => {
  const { store } = setup();
  await Promise.all(
    Array.from({ length: 20 }, (_, i) => store.addHighlight('https://example.com/a', { ...SEL, exact: `passage ${i}` })),
  );
  const page = await store.getPage('https://example.com/a');
  assert.equal(page.highlights.length, 20);
  assert.deepEqual(new Set(page.highlights.map((h) => h.exact)).size, 20);
});

test('removing the last highlight removes the page entry', async () => {
  const { store, area } = setup();
  await store.addHighlight('https://example.com/a', SEL);
  await store.addHighlight('https://example.com/a', { ...SEL, exact: 'second' });
  assert.deepEqual(await store.removeHighlight('https://example.com/a', 'h1'), { removed: true });
  assert.deepEqual((await store.getPage('https://example.com/a')).highlights.map((h) => h.id), ['h2']);
  await store.removeHighlight('https://example.com/a', 'h2');
  assert.equal(await store.getPage('https://example.com/a'), null);
  assert.equal(area.data.size, 0);
  assert.deepEqual(await store.removeHighlight('https://example.com/a', 'h2'), { removed: false });
});

test('getAll lists every page and ignores unrelated keys', async () => {
  const { store, area } = setup();
  await store.addHighlight('https://example.com/a', SEL);
  await store.addHighlight('https://example.org/b', SEL);
  area.data.set('settings', { theme: 'dark' });
  const pages = await store.getAll();
  assert.deepEqual(pages.map((p) => p.key).sort(), ['https://example.com/a', 'https://example.org/b']);
  await store.forgetPage('https://example.com/a');
  assert.deepEqual((await store.getAll()).map((p) => p.key), ['https://example.org/b']);
});

test('only web pages can have highlights', async () => {
  const { store } = setup();
  await assert.rejects(store.addHighlight('chrome://settings', SEL), /not a web page/);
  await assert.rejects(store.addHighlight('file:///tmp/a.html', SEL), /not a web page/);
});

test('a selector from a content script is checked before it is stored', () => {
  assert.deepEqual(cleanSelector({ ...SEL, extra: 'dropped' }), SEL);
  const bad = [
    [null, /missing/],
    [{ ...SEL, exact: '   ' }, /no text/],
    [{ ...SEL, exact: 'x'.repeat(5001) }, /too long/],
    [{ ...SEL, prefix: 'p'.repeat(65) }, /context too long/],
    [{ ...SEL, suffix: 7 }, /bad context/],
    [{ ...SEL, start: -1 }, /bad position/],
    [{ ...SEL, start: 10, end: 10 }, /bad position/],
    [{ ...SEL, start: '3' }, /bad position/],
  ];
  for (const [sel, message] of bad) assert.throws(() => cleanSelector(sel), message);
});

test('a failed write does not jam the queue for the next one', async () => {
  const { store } = setup();
  await assert.rejects(store.addHighlight('https://example.com/a', { exact: '' }));
  const ok = await store.addHighlight('https://example.com/a', SEL);
  assert.equal(ok.duplicate, false);
});
