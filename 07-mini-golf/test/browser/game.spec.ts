// The built game in a real browser: drawing, keyboard and mouse play, a whole round to the
// scorecard, and the bits that persist. Shots are the hole data's own known solutions, so a
// hole-in-one here proves the browser runs the same physics as the unit tests.

import { expect, test, type Page } from '@playwright/test';
import { HOLES } from '../../src/engine/holes.ts';
import { MAX_STROKES } from '../../src/engine/game.ts';

const COURSE_W = 960;
const COURSE_H = 600;

async function readAim(page: Page): Promise<{ angle: number; power: number }> {
  const text = (await page.locator('#aim-readout').textContent()) ?? '';
  const m = /Aim (\d+)° · Power (\d+)%/.exec(text);
  if (!m) throw new Error(`can't read aim from "${text}"`);
  return { angle: Number(m[1]), power: Number(m[2]) };
}

/** Set the aim with the arrow keys, as a keyboard player would. */
async function aimWithKeys(page: Page, angle: number, power: number): Promise<void> {
  const now = await readAim(page);
  let turn = ((((angle - now.angle) % 360) + 540) % 360) - 180;
  while (Math.abs(turn) >= 10) {
    await page.keyboard.press(turn > 0 ? 'Shift+ArrowRight' : 'Shift+ArrowLeft');
    turn += turn > 0 ? -10 : 10;
  }
  while (turn !== 0) {
    await page.keyboard.press(turn > 0 ? 'ArrowRight' : 'ArrowLeft');
    turn += turn > 0 ? -1 : 1;
  }
  let more = power - now.power;
  while (Math.abs(more) >= 10) {
    await page.keyboard.press(more > 0 ? 'Shift+ArrowUp' : 'Shift+ArrowDown');
    more += more > 0 ? -10 : 10;
  }
  while (more !== 0) {
    await page.keyboard.press(more > 0 ? 'ArrowUp' : 'ArrowDown');
    more += more > 0 ? -1 : 1;
  }
  expect(await readAim(page)).toEqual({ angle, power });
}

/** Course coordinates → page coordinates. */
async function toPage(page: Page, x: number, y: number): Promise<{ x: number; y: number }> {
  const box = (await page.locator('canvas').boundingBox())!;
  return { x: box.x + (x * box.width) / COURSE_W, y: box.y + (y * box.height) / COURSE_H };
}

/** A pixel from the canvas as drawn, at course coordinates. */
async function pixel(page: Page, x: number, y: number): Promise<[number, number, number]> {
  return page.locator('canvas').evaluate((c: HTMLCanvasElement, [px, py]) => {
    const d = c.getContext('2d')!.getImageData(Math.round((px! / 960) * c.width), Math.round((py! / 600) * c.height), 1, 1).data;
    return [d[0]!, d[1]!, d[2]!] as [number, number, number];
  }, [x, y]);
}

const phase = (page: Page) => page.locator('body').getAttribute('data-phase');

let errors: string[] = [];
test.beforeEach(async ({ page }) => {
  errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text());
  });
});
test.afterEach(() => {
  expect(errors, 'no errors in the console').toEqual([]);
});

test('the first hole is drawn: turf, cup and ball where the hole data says', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#hole-name')).toHaveText(HOLES[0]!.name);
  await expect(page.locator('#hole-par')).toHaveText(String(HOLES[0]!.par));
  await expect.poll(async () => (await pixel(page, 480, 260))[1]).toBeGreaterThan(130); // green turf
  const [r, g, b] = await pixel(page, 480, 260);
  expect(g).toBeGreaterThan(r + 40);
  expect(g).toBeGreaterThan(b + 40);
  const cup = await pixel(page, HOLES[0]!.cup.x, HOLES[0]!.cup.y);
  expect(Math.max(...cup)).toBeLessThan(60); // dark hole
  const ball = await pixel(page, HOLES[0]!.tee.x, HOLES[0]!.tee.y);
  expect(Math.min(...ball)).toBeGreaterThan(200); // white ball
});

test('the canvas stays sharp: its pixel size follows its displayed size', async ({ page }) => {
  await page.goto('/');
  for (const width of [1100, 700]) {
    await page.setViewportSize({ width, height: 820 });
    await expect
      .poll(() => page.locator('canvas').evaluate((c: HTMLCanvasElement) => c.width - Math.round(c.getBoundingClientRect().width * devicePixelRatio)))
      .toBe(0);
  }
});

test('a whole round: keyboard, mouse, the stroke limit, and the scorecard', async ({ page }) => {
  await page.goto('/');

  // Hole 1 with the keyboard: the known hole-in-one.
  const one = HOLES[0]!.solution[0]!;
  await aimWithKeys(page, one.angle, one.power);
  await page.keyboard.press('Space');
  await expect(page.locator('#hole-done')).toBeVisible();
  await expect(page.locator('#verdict')).toHaveText('Hole in one!');
  await expect(page.locator('#strokes')).toHaveText('1');
  await expect(page.locator('#next')).toBeFocused();
  await page.keyboard.press('Enter');

  // Hole 2 with the mouse: pull back from the ball (beyond full power) the opposite way to the shot.
  await expect(page.locator('#hole-number')).toHaveText('2');
  const two = HOLES[1]!.solution[0]!;
  const tee = HOLES[1]!.tee;
  const a = (two.angle * Math.PI) / 180;
  const from = await toPage(page, tee.x, tee.y);
  const to = await toPage(page, tee.x - Math.cos(a) * 200, tee.y - Math.sin(a) * 200);
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move(to.x, to.y, { steps: 8 });
  expect(await readAim(page)).toEqual({ angle: two.angle, power: two.power });
  await page.mouse.up();
  await expect(page.locator('body')).toHaveAttribute('data-phase', 'rolling');
  await expect(page.locator('#hole-done')).toBeVisible();
  await expect(page.locator('#verdict')).toHaveText('Hole in one!');
  await expect(page.locator('#total')).toHaveText('−2');
  await page.locator('#next').click();

  // Holes 3 to 9: seven gentle taps each, so every hole is picked up at the limit.
  for (let n = 3; n <= 9; n++) {
    await expect(page.locator('#hole-number')).toHaveText(String(n));
    await expect(page.locator('#hole-name')).toHaveText(HOLES[n - 1]!.name);
    await aimWithKeys(page, (await readAim(page)).angle, 1);
    for (let s = 1; s <= MAX_STROKES; s++) {
      await expect.poll(() => phase(page)).toBe('aiming');
      await page.keyboard.press('Space');
      await expect(page.locator('#strokes')).toHaveText(String(s));
    }
    await expect(page.locator('#hole-done')).toBeVisible();
    await expect(page.locator('#verdict')).toHaveText('Picked up');
    await expect(page.locator('#next')).toHaveText(n < 9 ? 'Next hole' : 'See the scorecard');
    await expect(page.locator('#next')).toBeFocused();
    await page.keyboard.press('Enter');
  }

  // The scorecard adds up: 1 + 1 + 7 × 7 = 51 against par 24.
  const card = page.locator('#round-done');
  await expect(card).toBeVisible();
  const rows = card.locator('tr');
  await expect(rows).toHaveCount(11); // header, nine holes, total
  await expect(rows.nth(1)).toContainText(HOLES[0]!.name);
  await expect(rows.last()).toHaveText(/Total\s*\+27\s*24\s*51/);
  await expect(page.locator('#best')).toHaveText('A new best round on this computer.');

  // Play again starts over, and the best round is remembered.
  await page.keyboard.press('Enter');
  await expect(page.locator('#hole-number')).toHaveText('1');
  await expect(page.locator('#strokes')).toHaveText('0');
  expect(await page.evaluate(() => localStorage.getItem('pocket-links:best'))).toBe('51');
});

test('a ball into the water costs a stroke and comes back to where it was hit', async ({ page }) => {
  // Hole 4 has a river; get there by playing 1 to 3 out quickly.
  await page.goto('/');
  for (let n = 1; n <= 3; n++) {
    await aimWithKeys(page, (await readAim(page)).angle, 1);
    for (let s = 1; s <= MAX_STROKES; s++) {
      await expect.poll(() => phase(page)).toBe('aiming');
      await page.keyboard.press('Space');
    }
    await expect(page.locator('#next')).toBeFocused();
    await page.keyboard.press('Enter');
  }
  await expect(page.locator('#hole-name')).toHaveText('The river');
  // Straight right from the tee (y 380) runs into the river below the bridge (y 268 to 332).
  await aimWithKeys(page, 0, 50);
  await page.keyboard.press('Space');
  await expect(page.locator('#strokes')).toHaveText('2'); // the shot and the penalty
  await expect.poll(() => phase(page)).toBe('aiming');
  const ball = await pixel(page, HOLES[3]!.tee.x, HOLES[3]!.tee.y);
  expect(Math.min(...ball)).toBeGreaterThan(200); // back on the tee
});

test('sound on or off is remembered across visits', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#mute')).toHaveText('Sound: on');
  await page.locator('#mute').click();
  await expect(page.locator('#mute')).toHaveText('Sound: off');
  await expect(page.locator('#mute')).toHaveAttribute('aria-pressed', 'true');
  await page.reload();
  await expect(page.locator('#mute')).toHaveText('Sound: off');
});
