// Make docs/popup.png and docs/library.png from the real extension in a real Chrome window.
// Dev only: needs an X display (xvfb-run on a server), xdotool and ImageMagick's `import`.
//
//   xvfb-run -a -s "-screen 0 1440x900x24" npm run screenshot
//
// popup.png: highlights saved on the original article, the page then edited, reloaded, and the
// toolbar popup opened (which is what puts the highlights back): two are flagged "changed slightly".

import { execFileSync } from 'node:child_process';
import { launch, startServer, selectText, press, until, sleep, ROOT } from '../test/browser/harness.js';
import { join } from 'node:path';

const DOCS = join(ROOT, 'docs');
const passages = [
  'write down what you see, not what you expected to see',
  'trimmed the wick',
  "By noon the next day the harbour master had heard about the tables, and by one o'clock the whole quay knew.",
];

const server = await startServer();
const b = await launch({ width: 1440, height: 900 });
const { page } = b;
try {
  await page.goto(server.url('/article'));
  for (const text of passages) {
    const before = Object.values(await b.pages())[0]?.highlights.length ?? 0;
    await selectText(page, text);
    await press('alt+shift+h');
    await until(async () => (Object.values(await b.pages())[0]?.highlights.length ?? 0) > before);
  }
  await page.goto(server.url('/late'));
  await page.getByText('closed for repairs').waitFor();
  await selectText(page, 'closed for repairs from Monday until the spring tides have passed');
  await press('alt+shift+h');
  await until(async () => Object.keys(await b.pages()).length === 2);

  server.serve('/article', 'article-edited.html');
  await page.goto(server.url('/article'));
  await page.evaluate(() => scrollTo(0, 250));
  const popup = await b.openPopup();
  await until(async () => (await b.painted())?.length === 3);
  await popup.evaluate('document.activeElement?.blur()');
  await sleep(600);
  execFileSync('import', ['-window', 'root', '-crop', '1440x780+0+0', join(DOCS, 'popup.png')]);
  await popup.close();

  const library = await b.context.newPage();
  await library.setViewportSize({ width: 1180, height: 760 });
  await library.goto(`chrome-extension://${b.id}/library/library.html`);
  await library.getByText('4 highlights on 2 pages').waitFor();
  await library.screenshot({ path: join(DOCS, 'library.png') });
} finally {
  await b.close();
  await server.close();
}

for (const name of ['popup.png', 'library.png']) {
  const file = join(DOCS, name);
  execFileSync('convert', [file, '-colors', '256', `PNG8:${file}`]);
  console.log(`docs/${name}`);
}
