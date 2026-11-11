// Syntax-check every script in the extension and the manifest's JSON (no dependencies).
//   npm run check
import { readdirSync, readFileSync } from 'node:fs';
import { join, relative } from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const walk = (dir) => readdirSync(dir, { withFileTypes: true }).flatMap((d) => (d.isDirectory() ? walk(join(dir, d.name)) : [join(dir, d.name)]));
const files = ['extension', 'test', 'scripts'].flatMap((d) => walk(join(root, d)));
let failed = 0;
for (const file of files.filter((f) => /\.(m?js)$/.test(f))) {
  try {
    execFileSync(process.execPath, ['--check', file], { stdio: 'pipe' });
  } catch (err) {
    failed++;
    console.error(`✗ ${relative(root, file)}\n${err.stderr}`);
  }
}
for (const file of files.filter((f) => f.endsWith('.json'))) {
  try {
    JSON.parse(readFileSync(file, 'utf8'));
  } catch (err) {
    failed++;
    console.error(`✗ ${relative(root, file)}: ${err.message}`);
  }
}
if (failed) process.exit(1);
console.log(`checked ${files.length} files`);
