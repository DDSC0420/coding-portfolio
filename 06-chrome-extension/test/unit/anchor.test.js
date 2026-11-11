// The anchoring rules, tested on plain strings. Every expected answer here is written from the
// scenario (what a reader would say is the same passage), not taken from the module's output.
import { test, describe as group } from 'node:test';
import assert from 'node:assert/strict';
import '../../extension/lib/anchor.js';

const A = globalThis.MarginAnchor;

const PAGE = [
  'The tide tables were wrong again on Tuesday.',
  'Mara checked them twice, then a third time, and the numbers still disagreed with the water.',
  'She wrote the discrepancy in the log, in pencil, because pencil survives a soaking.',
  'By noon the harbour master had heard, and by one o\'clock the whole quay knew.',
  'Nobody blamed the tables. Everybody blamed the moon, which was easier.',
  'The keeper\'s rule was simple: write down what you see, not what you expected to see.',
  'Mara had broken that rule exactly once, in her first winter, and the log still showed it.',
].join('\n\n');

// Select the n-th (0-based) occurrence of a phrase in raw text and describe it.
function select(raw, phrase, nth = 0) {
  let at = -1;
  for (let i = 0; i <= nth; i++) {
    at = raw.indexOf(phrase, at + 1);
    assert.notEqual(at, -1, `"${phrase}" occurrence ${nth} not in text`);
  }
  return { sel: A.describe(raw, at, at + phrase.length), at };
}

// What the anchored range says, with whitespace collapsed (that's what a reader sees).
function found(raw, sel) {
  const hit = A.anchor(raw, sel);
  if (!hit) return null;
  return { text: raw.slice(hit.start, hit.end).replace(/\s+/g, ' '), start: hit.start, method: hit.method };
}

// Small deterministic random source for the round-trip tests.
function rng(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 2 ** 32;
  };
}

group('normalize', () => {
  test('collapses every run of whitespace, including NBSP and line breaks, to one space', () => {
    const { text } = A.normalize('a \n\t b  c d');
    assert.equal(text, 'a b c d');
  });

  test('maps each normalised character back to where it came from', () => {
    const { text, map } = A.normalize('ab   cd');
    assert.equal(text, 'ab cd');
    assert.deepEqual(map, [0, 1, 2, 5, 6, 7]);
  });
});

group('describe', () => {
  test('keeps the quote and 32 characters of context either side', () => {
    const { sel } = select(PAGE, 'in pencil, because pencil survives a soaking');
    assert.equal(sel.exact, 'in pencil, because pencil survives a soaking');
    assert.equal(sel.prefix, 'e wrote the discrepancy in the log, '.slice(-32));
    assert.equal(sel.suffix, '. By noon the harbour master had');
    assert.equal(sel.prefix.length, 32);
    assert.equal(sel.suffix.length, 32);
  });

  test('measures in normalised text, so the paragraph break becomes one space', () => {
    const at = PAGE.indexOf('water.');
    const sel = A.describe(PAGE, at, at + 'water.\n\nShe wrote'.length);
    assert.equal(sel.exact, 'water. She wrote');
  });

  test('trims whitespace the selection picked up at either end', () => {
    const at = PAGE.indexOf('Tuesday.');
    const sel = A.describe(PAGE, at, at + 'Tuesday.\n\n'.length);
    assert.equal(sel.exact, 'Tuesday.');
  });

  test('the first passage on the page has an empty prefix; the last an empty suffix', () => {
    assert.equal(select(PAGE, 'The tide tables').sel.prefix, '');
    assert.equal(select(PAGE, 'the log still showed it.').sel.suffix, '');
  });

  test('refuses a selection with nothing visible in it, or a backwards one', () => {
    const at = PAGE.indexOf('\n\n');
    assert.equal(A.describe(PAGE, at, at + 2), null);
    assert.equal(A.describe(PAGE, 10, 10), null);
    assert.equal(A.describe(PAGE, 20, 10), null);
    assert.equal(A.describe(PAGE, -1, 10), null);
  });

  test('refuses a passage longer than 5000 characters', () => {
    const raw = 'word '.repeat(2000);
    assert.deepEqual(A.describe(raw, 0, raw.length), { error: 'too-long' });
  });
});

group('anchor: unchanged page', () => {
  test('finds a passage exactly where it was', () => {
    const { sel, at } = select(PAGE, 'the numbers still disagreed with the water');
    assert.deepEqual(found(PAGE, sel), { text: 'the numbers still disagreed with the water', start: at, method: 'exact' });
  });

  test('round trip: 300 random selections all come back to the same place', () => {
    const next = rng(7);
    for (let i = 0; i < 300; i++) {
      let a = Math.floor(next() * PAGE.length);
      let b = Math.floor(next() * PAGE.length);
      if (a > b) [a, b] = [b, a];
      const sel = A.describe(PAGE, a, b);
      if (!sel) continue;
      const hit = A.anchor(PAGE, sel);
      assert.ok(hit, `lost selection ${a}..${b}: "${sel.exact}"`);
      assert.equal(PAGE.slice(hit.start, hit.end).replace(/\s+/g, ' '), sel.exact);
      assert.equal(hit.method, 'exact');
    }
  });

  test('picks the right copy of a repeated phrase by its context', () => {
    // "pencil" appears twice in one sentence; "Mara" twice on the page
    const pencil = select(PAGE, 'pencil', 1);
    assert.equal(found(PAGE, pencil.sel).start, pencil.at);
    const mara = select(PAGE, 'Mara', 1);
    assert.equal(found(PAGE, mara.sel).start, mara.at);
  });
});

group('anchor: the page changed a little', () => {
  test('text added above shifts everything; still found, by the words not the position', () => {
    const { sel } = select(PAGE, 'Everybody blamed the moon');
    const edited = 'A new introduction paragraph, several sentences long, pushed in at the top. '.repeat(8) + PAGE;
    const hit = found(edited, sel);
    assert.equal(hit.text, 'Everybody blamed the moon');
    assert.equal(hit.start, edited.indexOf('Everybody blamed the moon'));
    assert.equal(hit.method, 'exact');
  });

  test('a repeated word stays on the right copy even after text is added above it', () => {
    const { sel } = select(PAGE, 'Mara', 1); // "Mara had broken that rule"
    const edited = 'Mara, Mara, Mara: an epigraph. ' + PAGE;
    const hit = found(edited, sel);
    assert.equal(edited.slice(hit.start, hit.start + 'Mara had broken'.length), 'Mara had broken');
  });

  test('re-flowed lines and non-breaking spaces make no difference', () => {
    const { sel } = select(PAGE, 'write down what you see, not what you expected to see');
    const edited = PAGE.replace('write down what you see, not what', 'write down\n   what you see,\nnot what');
    const hit = found(edited, sel);
    assert.equal(hit.text, 'write down what you see, not what you expected to see');
    assert.equal(hit.method, 'exact');
  });

  test('a typo fixed inside the passage: found by a close match', () => {
    const typo = PAGE.replace('harbour master had heard', 'harbuor master had herd');
    const { sel } = select(typo, 'By noon the harbuor master had herd, and by one o\'clock');
    const hit = found(PAGE, sel);
    assert.equal(hit.text, 'By noon the harbour master had heard, and by one o\'clock');
    assert.equal(hit.method, 'fuzzy');
  });

  test('one word changed in a short passage, words around it intact: found by context', () => {
    const { sel } = select(PAGE, 'checked them twice'); // 18 characters
    const edited = PAGE.replace('checked them twice', 'checked them again');
    const hit = found(edited, sel);
    assert.equal(hit.text, 'checked them again');
    assert.equal(hit.method, 'context');
  });

  test('a long passage with words changed in the middle is still found', () => {
    const passage = 'Mara checked them twice, then a third time, and the numbers still disagreed with the water.';
    const { sel } = select(PAGE, passage);
    assert.ok(sel.exact.length > 64, 'long enough to use the head-and-tail search');
    const edited = PAGE.replace('then a third time', 'then once more');
    const hit = found(edited, sel);
    assert.equal(hit.text, 'Mara checked them twice, then once more, and the numbers still disagreed with the water.');
    assert.equal(hit.method, 'fuzzy');
  });
});

group('anchor: the page changed a lot', () => {
  test('the passage was deleted: reported lost', () => {
    const { sel } = select(PAGE, 'Nobody blamed the tables. Everybody blamed the moon, which was easier.');
    const edited = PAGE.replace('Nobody blamed the tables. Everybody blamed the moon, which was easier.', '');
    assert.equal(A.anchor(edited, sel), null);
  });

  test('the passage was rewritten: reported lost, not matched to something else', () => {
    const { sel } = select(PAGE, 'She wrote the discrepancy in the log, in pencil');
    const edited = PAGE.replace(
      'She wrote the discrepancy in the log, in pencil, because pencil survives a soaking.',
      'Her notes went into the ledger in ink this time, which she would regret.',
    );
    assert.equal(A.anchor(edited, sel), null);
  });

  test('a short word whose sentence was rewritten is not moved to another copy of it', () => {
    // "the moon" also appears nowhere else, so plant a decoy elsewhere and remove the original sentence
    const { sel } = select(PAGE, 'the moon');
    const edited = PAGE.replace('Nobody blamed the tables. Everybody blamed the moon, which was easier.', 'Blame went elsewhere.')
      + '\n\nAn unrelated line about the moon over the bay.';
    assert.equal(A.anchor(edited, sel), null);
  });

  test('a long passage whose start and end were both rewritten is reported lost', () => {
    const passage = 'Mara checked them twice, then a third time, and the numbers still disagreed with the water.';
    const { sel } = select(PAGE, passage);
    const edited = PAGE.replace(passage, 'On the second look, then a third time, the numbers kept arguing with the sea level.');
    assert.equal(A.anchor(edited, sel), null);
  });
});

group('anchor: bad input and cost', () => {
  test('refuses malformed selectors instead of throwing', () => {
    for (const bad of [null, {}, { exact: '' }, { exact: 42 }, { exact: 'x'.repeat(5001) }, { exact: 'ok', prefix: 3 }]) {
      assert.equal(A.anchor(PAGE, bad), null);
    }
  });

  test('a lost 64-character passage on a 200,000-character page gives up quickly', () => {
    const big = (PAGE + '\n\n').repeat(Math.ceil(200000 / PAGE.length)).slice(0, 200000);
    const sel = { exact: 'A sentence of exactly sixty-four characters that is not there!!!', prefix: '', suffix: '' };
    assert.equal(sel.exact.length, 64);
    const t0 = performance.now();
    assert.equal(A.anchor(big, sel), null);
    assert.ok(performance.now() - t0 < 2000, 'took longer than 2 s');
  });
});

group('editDistance', () => {
  test('counts insertions, deletions and substitutions', () => {
    assert.equal(A.editDistance('kitten', 'sitting'), 3);
    assert.equal(A.editDistance('', 'abc'), 3);
    assert.equal(A.editDistance('abc', 'abc'), 0);
    assert.equal(A.editDistance('flaw', 'lawn'), 2);
  });

  test('with a limit, stops early and reports limit + 1', () => {
    assert.equal(A.editDistance('aaaaaaaaaa', 'bbbbbbbbbb', 3), 4);
    assert.equal(A.editDistance('kitten', 'sitting', 3), 3);
    assert.equal(A.editDistance('short', 'a much longer string', 2), 3);
  });
});
