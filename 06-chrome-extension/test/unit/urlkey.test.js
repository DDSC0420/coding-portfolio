import { test } from 'node:test';
import assert from 'node:assert/strict';
import { urlKey, originPattern, patternHost } from '../../extension/lib/urlkey.js';

test('one page, one key: the #fragment and tracking parameters are dropped', () => {
  const key = 'https://example.com/post?id=7';
  assert.equal(urlKey('https://example.com/post?id=7#comments'), key);
  assert.equal(urlKey('https://example.com/post?utm_source=x&id=7&utm_medium=email&fbclid=abc'), key);
  assert.equal(urlKey('https://example.com/post?gclid=1&id=7&mc_cid=2&msclkid=3'), key);
});

test('query order does not matter, but query values do', () => {
  assert.equal(urlKey('https://example.com/s?b=2&a=1'), urlKey('https://example.com/s?a=1&b=2'));
  assert.notEqual(urlKey('https://example.com/s?page=2'), urlKey('https://example.com/s?page=3'));
});

test('an app route in the fragment (#/ or #!) is part of the page', () => {
  assert.equal(urlKey('https://app.example.com/#/notes/12'), 'https://app.example.com/#/notes/12');
  assert.equal(urlKey('https://app.example.com/#!/inbox'), 'https://app.example.com/#!/inbox');
  assert.equal(urlKey('https://app.example.com/#top'), 'https://app.example.com/');
});

test('host case and default ports are normalised; other ports are kept', () => {
  assert.equal(urlKey('HTTPS://Example.COM:443/A'), 'https://example.com/A');
  assert.equal(urlKey('http://127.0.0.1:8080/x'), 'http://127.0.0.1:8080/x');
});

test('anything that is not an http(s) page has no key', () => {
  for (const href of ['chrome://extensions', 'file:///home/me/a.html', 'about:blank', 'not a url', '', undefined]) {
    assert.equal(urlKey(href), null, String(href));
  }
});

test('the permission pattern covers the site on any port, nothing broader', () => {
  assert.equal(originPattern('https://Docs.Example.com:8443/a/b?c#d'), 'https://docs.example.com/*');
  assert.equal(originPattern('http://127.0.0.1:5173/'), 'http://127.0.0.1/*');
  assert.equal(originPattern('chrome://settings'), null);
  assert.equal(originPattern('nonsense'), null);
});

test('a pattern is shown to people as its host', () => {
  assert.equal(patternHost('https://docs.example.com/*'), 'docs.example.com');
  assert.equal(patternHost('*://*.example.org/*'), '*.example.org');
  assert.equal(patternHost('<all_urls>'), '<all_urls>');
});
