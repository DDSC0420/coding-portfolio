import { test } from 'node:test';
import assert from 'node:assert/strict';
import { filterPages, countHighlights, toMarkdown } from '../../extension/lib/library.js';

const h = (id, exact, start, createdAt = '2026-11-10T10:00:00.000Z') => ({ id, exact, prefix: '', suffix: '', start, end: start + exact.length, createdAt });

const PAGES = [
  {
    key: 'https://example.com/tides',
    url: 'https://example.com/tides',
    title: 'Why the tide tables were wrong',
    updatedAt: '2026-11-10T10:00:00.000Z',
    highlights: [h('b', 'pencil survives a soaking', 400), h('a', 'the numbers still disagreed with the water', 120)],
  },
  {
    key: 'https://en.example.org/wiki/Lighthouse_(disambiguation)',
    url: 'https://en.example.org/wiki/Lighthouse_(disambiguation)',
    title: 'Lighthouse [list]',
    updatedAt: '2026-11-11T08:00:00.000Z',
    highlights: [h('c', '# not a heading, *not* emphasis & <b>not</b> HTML', 10)],
  },
];

test('newest page first; highlights in the order they appear on the page', () => {
  const pages = filterPages(PAGES, '');
  assert.deepEqual(pages.map((p) => p.title), ['Lighthouse [list]', 'Why the tide tables were wrong']);
  assert.deepEqual(pages[1].highlights.map((x) => x.id), ['a', 'b']);
  assert.equal(countHighlights(pages), 3);
});

test('search matches every word, in the quote, the title or the address, ignoring case', () => {
  assert.deepEqual(filterPages(PAGES, 'PENCIL').flatMap((p) => p.highlights.map((x) => x.id)), ['b']);
  assert.deepEqual(filterPages(PAGES, 'tide water').flatMap((p) => p.highlights.map((x) => x.id)), ['a']);
  assert.deepEqual(filterPages(PAGES, 'tables').flatMap((p) => p.highlights.map((x) => x.id)), ['a', 'b']); // title
  assert.deepEqual(filterPages(PAGES, 'example.org').flatMap((p) => p.highlights.map((x) => x.id)), ['c']); // address
  assert.deepEqual(filterPages(PAGES, 'pencil lighthouse'), []);
});

test('Markdown export: a heading per page, a quote per highlight, nothing in a quote becomes formatting', () => {
  const md = toMarkdown(filterPages(PAGES, ''), { exportedAt: new Date('2026-11-11T12:00:00Z') });
  const expected = [
    '# Margin highlights',
    '',
    'Exported 2026-11-11 · 2 pages · 3 highlights',
    '',
    '## [Lighthouse \\[list\\]](<https://en.example.org/wiki/Lighthouse_(disambiguation)>)',
    '',
    '> \\# not a heading, \\*not\\* emphasis \\& \\<b\\>not\\</b\\> HTML',
    '',
    '## [Why the tide tables were wrong](https://example.com/tides)',
    '',
    '> the numbers still disagreed with the water',
    '',
    '> pencil survives a soaking',
    '',
  ].join('\n');
  assert.equal(md, expected);
});

test('an export of search results says what it was filtered by', () => {
  const md = toMarkdown(filterPages(PAGES, 'pencil'), { exportedAt: new Date('2026-11-11T12:00:00Z'), query: ' pencil ' });
  assert.match(md, /^Exported 2026-11-11 matching "pencil" · 1 page · 1 highlight$/m);
});

test('list-like starts are escaped too', () => {
  const md = toMarkdown([{ url: 'https://x.test/', title: '', highlights: [h('d', '1. first', 0), h('e', '- dash', 9), h('f', '+ plus', 20)] }], {
    exportedAt: new Date('2026-11-11T00:00:00Z'),
  });
  assert.match(md, /^> 1\\\. first$/m);
  assert.match(md, /^> \\- dash$/m);
  assert.match(md, /^> \\\+ plus$/m);
  assert.match(md, /^## \[https:\/\/x\.test\/\]\(https:\/\/x\.test\/\)$/m); // no title: the address stands in
});
