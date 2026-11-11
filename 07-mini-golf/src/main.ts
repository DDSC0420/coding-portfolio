// Wiring: the round, the canvas, input, sound and the score panels.

import { HOLES } from './engine/holes.ts';
import { MAX_STROKES, Round, type GameEvent } from './engine/game.ts';
import { startLoop } from './loop.ts';
import { COURSE_H, COURSE_W, drawFrame, paintCourse } from './render/draw.ts';
import { Effects } from './render/effects.ts';
import { Sounds } from './audio.ts';
import { scoreName, toPar } from './scoring.ts';
import { aimFromDrag, aimFromKey } from './aim.ts';

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
const canvas = $<HTMLCanvasElement>('course');
const ctx = canvas.getContext('2d')!;
const BEST_KEY = 'pocket-links:best';

// In development only, ?hole=N starts at hole N (for working on one hole).
const devStart = import.meta.env.DEV ? Number(new URLSearchParams(location.search).get('hole') ?? 1) - 1 : 0;
let round = new Round(import.meta.env.DEV && devStart > 0 ? HOLES.slice(devStart) : HOLES);
let course = paintCourse(round.hole);
let prev = { x: round.ball.x, y: round.ball.y };
let trail: Array<{ x: number; y: number }> = [];
let steps = 0;
let lastDraw = 0;
const effects = new Effects();
const sounds = new Sounds();

// ---- canvas size: sharp at any size and pixel density ----

function fitCanvas(): void {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(1, Math.round(rect.width * dpr));
  const h = Math.max(1, Math.round(rect.height * dpr));
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w;
    canvas.height = h;
  }
}
new ResizeObserver(fitCanvas).observe(canvas);

function toCourse(e: PointerEvent): { x: number; y: number } {
  const rect = canvas.getBoundingClientRect();
  return { x: ((e.clientX - rect.left) * COURSE_W) / rect.width, y: ((e.clientY - rect.top) * COURSE_H) / rect.height };
}

// ---- what the round reports ----

function nearestBumper(x: number, y: number): number {
  let best = -1;
  let bestD = Infinity;
  (round.hole.bumpers ?? []).forEach((b, i) => {
    const d = Math.hypot(b.at.x - x, b.at.y - y);
    if (d < bestD) {
      bestD = d;
      best = i;
    }
  });
  return best;
}

function handle(events: GameEvent[]): void {
  for (const e of events) {
    switch (e.type) {
      case 'contact':
        sounds.play(e.contact.kind === 'bumper' ? 'bumper' : 'wall', e.contact.speed / 700);
        if (e.contact.kind === 'bumper') {
          effects.flash(nearestBumper(e.x, e.y));
          effects.burst(e.x, e.y, 'spark');
        }
        break;
      case 'water':
        sounds.play('splash');
        effects.burst(e.x, e.y, 'splash');
        trail = [];
        break;
      case 'holed':
        trail = [];
        sounds.play('holed');
        effects.burst(round.hole.cup.x, round.hole.cup.y, 'confetti');
        setTimeout(showHoleDone, 900);
        break;
      case 'picked-up':
        setTimeout(showHoleDone, 500);
        break;
      case 'stopped':
        trail = [];
        break;
      case 'shot':
        break;
    }
  }
  if (events.length) refreshHud();
}

function update(): void {
  prev = { x: round.ball.x, y: round.ball.y };
  const events = round.update();
  steps++;
  if (round.phase === 'rolling' && steps % 3 === 0) {
    trail.push({ x: round.ball.x, y: round.ball.y });
    if (trail.length > 26) trail.shift();
  }
  if (events.length) handle(events);
}

function draw(alpha: number, now: number): void {
  const dt = lastDraw ? Math.min(0.1, (now - lastDraw) / 1000) : 0;
  lastDraw = now;
  effects.update(dt);
  ctx.setTransform(canvas.width / COURSE_W, 0, 0, canvas.height / COURSE_H, 0, 0);
  drawFrame(ctx, { round, course, prev, alpha, now, trail, effects, showAim: true });
}

// ---- HUD and panels ----

function refreshHud(): void {
  $('hole-number').textContent = String(round.index + 1);
  $('hole-name').textContent = round.hole.name;
  $('hole-par').textContent = String(round.hole.par);
  $('strokes').textContent = String(round.strokes);
  $('total').textContent = round.scores.length ? toPar(round.total, round.parSoFar) : 'E';
  $('aim-readout').textContent = `Aim ${round.aim.angle}° · Power ${round.aim.power}%`;
  document.body.dataset.phase = round.phase;
  document.body.dataset.hole = String(round.index + 1);
}

function showHoleDone(): void {
  const score = round.scores[round.index] ?? MAX_STROKES;
  const picked = round.phase === 'picked-up';
  $('verdict').textContent = picked ? 'Picked up' : scoreName(score, round.hole.par);
  $('verdict-detail').textContent = picked
    ? `${MAX_STROKES} strokes is the limit, so the hole scores ${MAX_STROKES}.`
    : `${score} stroke${score === 1 ? '' : 's'} on a par ${round.hole.par}.`;
  $('next').textContent = round.index + 1 < round.holes.length ? 'Next hole' : 'See the scorecard';
  $('hole-done').hidden = false;
  document.body.dataset.phase = 'hole-done';
  $('next').focus();
}

function bestRound(total: number): { best: number; isNew: boolean } {
  let best = Infinity;
  try {
    best = Number(localStorage.getItem(BEST_KEY) ?? Infinity);
  } catch {
    /* storage blocked: no best score kept */
  }
  const isNew = total < best;
  if (isNew) {
    try {
      localStorage.setItem(BEST_KEY, String(total));
    } catch {
      /* ignore */
    }
  }
  return { best: Math.min(best, total), isNew };
}

function showScorecard(): void {
  const table = $<HTMLTableElement>('card');
  table.replaceChildren();
  const head = table.insertRow();
  for (const label of ['Hole', '', 'Par', 'Score']) {
    const th = document.createElement('th');
    th.textContent = label;
    head.append(th);
  }
  round.holes.forEach((hole, i) => {
    const row = table.insertRow();
    const score = round.scores[i]!;
    row.insertCell().textContent = String(i + 1);
    row.insertCell().textContent = hole.name;
    row.insertCell().textContent = String(hole.par);
    const cell = row.insertCell();
    cell.textContent = String(score);
    if (score < hole.par) cell.className = 'under';
    else if (score > hole.par) cell.className = 'over';
  });
  const par = round.holes.reduce((a, h) => a + h.par, 0);
  const total = table.insertRow();
  total.className = 'total';
  total.insertCell().textContent = 'Total';
  total.insertCell().textContent = toPar(round.total, par);
  total.insertCell().textContent = String(par);
  total.insertCell().textContent = String(round.total);
  const { best, isNew } = bestRound(round.total);
  $('best').textContent = isNew ? 'A new best round on this computer.' : `Best round on this computer: ${best} (${toPar(best, par)}).`;
  $('round-done').hidden = false;
  document.body.dataset.phase = 'finished';
  $('again').focus();
}

$('next').addEventListener('click', () => {
  $('hole-done').hidden = true;
  if (round.nextHole()) {
    course = paintCourse(round.hole);
    prev = { x: round.ball.x, y: round.ball.y };
    trail = [];
    refreshHud();
    canvas.focus();
  } else {
    showScorecard();
  }
});

$('again').addEventListener('click', () => {
  $('round-done').hidden = true;
  round = new Round(HOLES);
  course = paintCourse(round.hole);
  prev = { x: round.ball.x, y: round.ball.y };
  trail = [];
  refreshHud();
  canvas.focus();
});

const mute = $('mute');
const showMute = () => {
  mute.textContent = sounds.muted ? 'Sound: off' : 'Sound: on';
  mute.setAttribute('aria-pressed', String(sounds.muted));
};
mute.addEventListener('click', () => {
  sounds.unlock();
  sounds.toggle();
  showMute();
});
showMute();

// ---- input: drag back and let go, or the keyboard ----

function putt(): void {
  sounds.unlock();
  const events = round.shoot();
  if (events.length) {
    sounds.play('putt', round.aim.power / 100);
    trail = [];
    handle(events);
  }
}

let dragging = false;
let aimBeforeDrag = round.aim;

canvas.addEventListener('pointerdown', (e) => {
  if (round.phase !== 'aiming' || e.button !== 0) return;
  sounds.unlock();
  dragging = true;
  aimBeforeDrag = round.aim;
  canvas.setPointerCapture(e.pointerId);
});

canvas.addEventListener('pointermove', (e) => {
  if (!dragging) return;
  const aim = aimFromDrag(round.ball, toCourse(e));
  if (aim) {
    round.setAim(aim.angle, aim.power);
    refreshHud();
  }
});

canvas.addEventListener('pointerup', (e) => {
  if (!dragging) return;
  dragging = false;
  if (aimFromDrag(round.ball, toCourse(e))) putt();
});

canvas.addEventListener('pointercancel', () => {
  dragging = false;
  round.setAim(aimBeforeDrag.angle, aimBeforeDrag.power);
  refreshHud();
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && dragging) {
    dragging = false;
    round.setAim(aimBeforeDrag.angle, aimBeforeDrag.power);
    refreshHud();
    return;
  }
  if (round.phase !== 'aiming' || e.target instanceof HTMLButtonElement) return;
  const next = aimFromKey(round.aim, e.key, e.shiftKey);
  if (next) {
    e.preventDefault();
    round.setAim(next.angle, next.power);
    refreshHud();
  } else if (e.key === ' ' || e.key === 'Enter') {
    e.preventDefault();
    putt();
  }
});

refreshHud();
canvas.focus();
startLoop(update, draw);
