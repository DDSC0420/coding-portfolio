// Guards on what the extension asks for and loads. These back the README's privacy claims.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, existsSync } from 'node:fs';
import { join, dirname, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const EXT = fileURLToPath(new URL('../../extension/', import.meta.url));
const manifest = JSON.parse(readFileSync(join(EXT, 'manifest.json'), 'utf8'));
const pkg = JSON.parse(readFileSync(new URL('../../package.json', import.meta.url), 'utf8'));

function walk(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((d) =>
    d.isDirectory() ? walk(join(dir, d.name)) : [join(dir, d.name)],
  );
}
const files = walk(EXT);
const code = files.filter((f) => f.endsWith('.js'));

test('Manifest V3, same version as package.json', () => {
  assert.equal(manifest.manifest_version, 3);
  assert.equal(manifest.version, pkg.version);
});

test('asks only for activeTab, contextMenus, storage and scripting up front', () => {
  assert.deepEqual([...manifest.permissions].sort(), ['activeTab', 'contextMenus', 'scripting', 'storage']);
  assert.equal(manifest.host_permissions, undefined, 'no site access at install');
  assert.equal(manifest.content_scripts, undefined, 'nothing runs on pages by default');
});

test('site access is optional, per site, web pages only', () => {
  assert.deepEqual(manifest.optional_host_permissions, ['http://*/*', 'https://*/*']);
  assert.equal(manifest.optional_permissions, undefined);
});

test('nothing exposed to web pages or other extensions, and the default CSP is kept', () => {
  assert.equal(manifest.web_accessible_resources, undefined);
  assert.equal(manifest.externally_connectable, undefined);
  assert.equal(manifest.content_security_policy, undefined);
});

test('the extension code makes no network requests', () => {
  const network = /\bfetch\s*\(|XMLHttpRequest|WebSocket|EventSource|sendBeacon|importScripts|https?:\/\/(?!www\.w3\.org)/;
  for (const file of code) {
    const source = readFileSync(file, 'utf8').replace(/^\s*(\/\/|\*|\/\*).*$/gm, ''); // ignore comments
    assert.doesNotMatch(source, network, relative(EXT, file));
  }
});

test('no remote code: every script a page loads is a local file', () => {
  for (const html of files.filter((f) => f.endsWith('.html'))) {
    const source = readFileSync(html, 'utf8');
    assert.doesNotMatch(source, /<script(?![^>]*\bsrc=)[^>]*>/, `${relative(EXT, html)} has an inline script`);
    for (const [, src] of source.matchAll(/\b(?:src|href)="([^"]+)"/g)) {
      assert.ok(existsSync(join(dirname(html), src)), `${relative(EXT, html)} → ${src} is missing`);
    }
  }
});

test('every file the manifest and the scripts point at exists', () => {
  const referenced = [
    manifest.background.service_worker,
    manifest.action.default_popup,
    ...Object.values(manifest.icons),
    ...Object.values(manifest.action.default_icon),
  ];
  const background = readFileSync(join(EXT, 'background.js'), 'utf8');
  for (const [, list] of background.matchAll(/const CONTENT_(?:JS|CSS) = \[([^\]]+)\]/g)) {
    referenced.push(...[...list.matchAll(/'([^']+)'/g)].map((m) => m[1]));
  }
  assert.ok(referenced.includes('lib/anchor.js') && referenced.includes('content.js') && referenced.includes('content.css'));
  for (const file of referenced) assert.ok(existsSync(join(EXT, file)), `${file} is missing`);
  for (const file of code) {
    for (const [, spec] of readFileSync(file, 'utf8').matchAll(/^import .* from '([^']+)';$/gm)) {
      assert.ok(existsSync(join(dirname(file), spec)), `${relative(EXT, file)} imports missing ${spec}`);
    }
  }
});

test('both keyboard shortcuts are declared', () => {
  assert.equal(manifest.commands['save-highlight'].suggested_key.default, 'Alt+Shift+H');
  assert.equal(manifest.commands._execute_action.suggested_key.default, 'Alt+Shift+M');
});
