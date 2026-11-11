// Make docs/play.png (the island hole, mid-roll) and docs/scorecard.png (a full round) from the
// real game. Dev only: needs Playwright's browser (or CHROME_PATH). Starts its own dev server.
//
//   npm run screenshot

import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const port = 5188;
const server = spawn('npx', ['vite', '--port', String(port), '--strictPort'], { cwd: root, stdio: 'ignore' });
await new Promise((r) => setTimeout(r, 2500));

const browser = await chromium.launch(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {});
const page = await browser.newPage({ viewport: { width: 1180, height: 860 }, deviceScaleFactor: 2 });

async function readAim() {
  const m = /Aim (\d+)° · Power (\d+)%/.exec(await page.textContent('#aim-readout'));
  return { angle: Number(m[1]), power: Number(m[2]) };
}
async function aim(angle, power) {
  const now = await readAim();
  let turn = ((((angle - now.angle) % 360) + 540) % 360) - 180;
  while (turn) {
    const big = Math.abs(turn) >= 10;
    await page.keyboard.press(`${big ? 'Shift+' : ''}${turn > 0 ? 'ArrowRight' : 'ArrowLeft'}`);
    turn += (turn > 0 ? -1 : 1) * (big ? 10 : 1);
  }
  let more = power - now.power;
  while (more) {
    const big = Math.abs(more) >= 10;
    await page.keyboard.press(`${big ? 'Shift+' : ''}${more > 0 ? 'ArrowUp' : 'ArrowDown'}`);
    more += (more > 0 ? -1 : 1) * (big ? 10 : 1);
  }
}
const phase = () => page.getAttribute('body', 'data-phase');

// Where the ball is, found the way a person finds it: the white blob on the canvas (away from the flag).
async function ballOnCanvas(cup) {
  return page.locator('canvas').evaluate((c, cup) => {
    const { width, height } = c;
    const d = c.getContext('2d').getImageData(0, 0, width, height).data;
    let sx = 0;
    let sy = 0;
    let n = 0;
    for (let y = 0; y < height; y += 2) {
      for (let x = 0; x < width; x += 2) {
        const i = (y * width + x) * 4;
        if (d[i] > 238 && d[i + 1] > 238 && d[i + 2] > 238) {
          const cx = (x / width) * 960;
          const cy = (y / height) * 600;
          if (Math.hypot(cx - cup.x, cy - cup.y + 30) < 45) continue; // the flagpole
          sx += cx;
          sy += cy;
          n++;
        }
      }
    }
    return n ? { x: sx / n, y: sy / n } : null;
  }, cup);
}
async function waitFor(fn) {
  for (let i = 0; i < 400; i++) {
    if (await fn()) return;
    await page.waitForTimeout(50);
  }
  throw new Error('timed out');
}

try {
  // A round played with the known solutions. The windmill's timing can't be matched by hand,
  // so there it putts at the cup with roughly the right strength until it drops.
  // play.png is caught mid-roll on the last hole; scorecard.png at the end.
  const { HOLES } = await import('../src/engine/holes.ts');
  await page.goto(`http://127.0.0.1:${port}/`);
  await page.waitForTimeout(300);
  for (let n = 0; n < 9; n++) {
    const hole = HOLES[n];
    for (let s = 0; (await phase()) === 'aiming'; s++) {
      let shot = hole.solution[s];
      if (!shot || hole.spinners?.length) {
        // on the windmill: through the gap first, then at the cup, with about the right strength
        const ball = (await ballOnCanvas(hole.cup)) ?? hole.tee;
        const target = ball.x < 470 ? { x: 520, y: 300 } : hole.cup;
        const dist = Math.hypot(target.x - ball.x, target.y - ball.y) + (ball.x < 470 ? 260 : 0);
        const angle = (Math.round((Math.atan2(target.y - ball.y, target.x - ball.x) * 180) / Math.PI) + 360) % 360;
        shot = { angle, power: Math.max(8, Math.min(100, Math.round(Math.sqrt(460 * dist) / 10 + 6))) };
      }
      await aim(shot.angle, shot.power);
      await page.keyboard.press('Space');
      if (n === 8 && s === 0) {
        await page.waitForTimeout(1150);
        await page.evaluate(() => document.activeElement?.blur());
        await page.screenshot({ path: `${root}docs/play.png` });
      }
      await waitFor(async () => (await phase()) !== 'rolling');
      await page.waitForTimeout(100);
    }
    await waitFor(async () => (await page.locator('#next').isVisible()));
    await page.locator('#next').click();
  }
  await page.locator('#round-done').waitFor();
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${root}docs/scorecard.png` });
} finally {
  await browser.close();
  server.kill();
}
console.log('docs/play.png, docs/scorecard.png');
