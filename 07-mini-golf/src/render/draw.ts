// Drawing a hole on a 2D canvas. The parts that never move (turf, sand, walls) are painted once
// per hole into an offscreen canvas; each frame copies that and draws the moving parts on top.

import { BALL_R, CUP_R, blades, BLADE, STEP } from '../engine/physics.ts';
import type { HoleDef, Polygon, Zone } from '../engine/types.ts';
import type { Round } from '../engine/game.ts';
import type { Effects } from './effects.ts';

export const COURSE_W = 960;
export const COURSE_H = 600;

const C = {
  bg1: '#0d2621',
  bg2: '#143a31',
  turf: '#3d9a4b',
  turfStripe: 'rgba(255,255,255,0.055)',
  rim: '#e8dcc2',
  rimShade: '#b9a988',
  block: '#d9caa9',
  blockTop: '#efe4cc',
  sand: '#e6cf8f',
  sandDot: 'rgba(150,115,50,0.35)',
  ice: '#d3f1fb',
  water: '#2f7fc0',
  waterDeep: '#22659e',
  cup: '#0f1a12',
  flag: '#e84a4a',
  bumper: '#e2483d',
  blade: '#8a5a33',
} as const;

function trace(ctx: CanvasRenderingContext2D, poly: Polygon): void {
  ctx.beginPath();
  poly.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
  ctx.closePath();
}

// Deterministic pseudo-random numbers for texture speckles, so a hole looks the same every time.
function speckle(seed: number): () => number {
  let s = seed;
  return () => {
    s = (s * 16807) % 2147483647;
    return s / 2147483647;
  };
}

function turf(ctx: CanvasRenderingContext2D, poly: Polygon): void {
  ctx.save();
  trace(ctx, poly);
  ctx.fillStyle = C.turf;
  ctx.fill();
  ctx.clip();
  ctx.fillStyle = C.turfStripe;
  for (let x = 0; x < COURSE_W; x += 64) ctx.fillRect(x, 0, 32, COURSE_H);
  ctx.restore();
}

function paintZone(ctx: CanvasRenderingContext2D, zone: Zone, index: number): void {
  switch (zone.kind) {
    case 'green':
      turf(ctx, zone.shape);
      ctx.save();
      trace(ctx, zone.shape);
      ctx.strokeStyle = 'rgba(20,60,30,0.45)';
      ctx.lineWidth = 3;
      ctx.stroke();
      ctx.restore();
      break;
    case 'sand': {
      ctx.save();
      trace(ctx, zone.shape);
      ctx.fillStyle = C.sand;
      ctx.fill();
      ctx.clip();
      const rnd = speckle(index * 7919 + 17);
      ctx.fillStyle = C.sandDot;
      for (let i = 0; i < 900; i++) ctx.fillRect(rnd() * COURSE_W, rnd() * COURSE_H, 1.6, 1.6);
      ctx.restore();
      ctx.save();
      trace(ctx, zone.shape);
      ctx.strokeStyle = 'rgba(255,248,220,0.7)';
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.restore();
      break;
    }
    case 'ice': {
      ctx.save();
      trace(ctx, zone.shape);
      ctx.fillStyle = C.ice;
      ctx.fill();
      ctx.clip();
      ctx.strokeStyle = 'rgba(255,255,255,0.8)';
      ctx.lineWidth = 2;
      const rnd = speckle(index * 104729 + 3);
      for (let i = 0; i < 40; i++) {
        const x = rnd() * COURSE_W;
        const y = rnd() * COURSE_H;
        ctx.beginPath();
        ctx.moveTo(x, y);
        ctx.lineTo(x + 18, y - 10);
        ctx.stroke();
      }
      ctx.restore();
      break;
    }
    case 'slope':
      // drawn as turf here; its moving chevrons are added every frame
      turf(ctx, zone.shape);
      break;
    case 'water':
      ctx.save();
      trace(ctx, zone.shape);
      const g = ctx.createLinearGradient(0, 0, 0, COURSE_H);
      g.addColorStop(0, C.water);
      g.addColorStop(1, C.waterDeep);
      ctx.fillStyle = g;
      ctx.fill();
      ctx.restore();
      break;
  }
}

/** Paint the parts of a hole that never move. */
export function paintCourse(hole: HoleDef): HTMLCanvasElement {
  const canvas = document.createElement('canvas');
  const scale = 2; // painted at 2× so it stays sharp on high-density screens
  canvas.width = COURSE_W * scale;
  canvas.height = COURSE_H * scale;
  const ctx = canvas.getContext('2d')!;
  ctx.scale(scale, scale);
  ctx.lineJoin = 'round';

  // surroundings
  const bg = ctx.createLinearGradient(0, 0, COURSE_W, COURSE_H);
  bg.addColorStop(0, C.bg1);
  bg.addColorStop(1, C.bg2);
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, COURSE_W, COURSE_H);
  ctx.fillStyle = 'rgba(255,255,255,0.025)';
  for (let x = -COURSE_H; x < COURSE_W; x += 22) {
    ctx.beginPath();
    ctx.moveTo(x, COURSE_H);
    ctx.lineTo(x + COURSE_H, 0);
    ctx.lineTo(x + COURSE_H + 8, 0);
    ctx.lineTo(x + 8, COURSE_H);
    ctx.fill();
  }

  // shadow under the course, then the turf and zones
  ctx.save();
  ctx.shadowColor = 'rgba(0,0,0,0.45)';
  ctx.shadowBlur = 24;
  ctx.shadowOffsetY = 10;
  trace(ctx, hole.boundary);
  ctx.fillStyle = C.turf;
  ctx.fill();
  ctx.restore();
  turf(ctx, hole.boundary);
  ctx.save();
  trace(ctx, hole.boundary); // zones only exist inside the course
  ctx.clip();
  (hole.zones ?? []).forEach((z, i) => paintZone(ctx, z, i));
  ctx.restore();

  // the outer wall, drawn just outside the line the ball bounces off
  ctx.save();
  ctx.beginPath();
  ctx.rect(-50, -50, COURSE_W + 100, COURSE_H + 100);
  hole.boundary.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
  ctx.closePath();
  ctx.clip('evenodd');
  trace(ctx, hole.boundary);
  ctx.lineWidth = 26;
  ctx.strokeStyle = C.rimShade;
  ctx.stroke();
  ctx.lineWidth = 20;
  ctx.strokeStyle = C.rim;
  ctx.stroke();
  ctx.restore();
  // a soft inner shadow along the wall
  ctx.save();
  trace(ctx, hole.boundary);
  ctx.clip();
  trace(ctx, hole.boundary);
  ctx.lineWidth = 9;
  ctx.strokeStyle = 'rgba(0,0,0,0.16)';
  ctx.stroke();
  ctx.restore();

  // solid blocks with a shadow and a lighter top
  for (const wall of hole.walls ?? []) {
    ctx.save();
    ctx.shadowColor = 'rgba(0,0,0,0.3)';
    ctx.shadowBlur = 8;
    ctx.shadowOffsetX = 3;
    ctx.shadowOffsetY = 5;
    trace(ctx, wall);
    ctx.fillStyle = C.block;
    ctx.fill();
    ctx.restore();
    ctx.save();
    trace(ctx, wall);
    ctx.clip();
    ctx.translate(-2, -3);
    trace(ctx, wall);
    ctx.fillStyle = C.blockTop;
    ctx.fill();
    ctx.restore();
  }

  // tee mat
  ctx.fillStyle = 'rgba(15,60,30,0.55)';
  ctx.beginPath();
  ctx.roundRect(hole.tee.x - 16, hole.tee.y - 16, 32, 32, 6);
  ctx.fill();
  ctx.strokeStyle = 'rgba(255,255,255,0.25)';
  ctx.lineWidth = 1.5;
  ctx.stroke();

  // the cup
  const cup = ctx.createRadialGradient(hole.cup.x - 2, hole.cup.y - 2, 2, hole.cup.x, hole.cup.y, CUP_R);
  cup.addColorStop(0, '#000');
  cup.addColorStop(1, '#203024');
  ctx.fillStyle = cup;
  ctx.beginPath();
  ctx.arc(hole.cup.x, hole.cup.y, CUP_R, 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = 'rgba(255,255,255,0.55)';
  ctx.lineWidth = 1.5;
  ctx.stroke();
  return canvas;
}

export interface FrameInput {
  round: Round;
  course: HTMLCanvasElement;
  prev: { x: number; y: number };
  alpha: number;
  now: number; // ms
  trail: Array<{ x: number; y: number }>;
  effects: Effects;
  showAim: boolean;
}

function drawWater(ctx: CanvasRenderingContext2D, hole: HoleDef, now: number): void {
  const t = now / 1000;
  ctx.save();
  trace(ctx, hole.boundary);
  ctx.clip();
  for (const z of hole.zones ?? []) {
    if (z.kind !== 'water') continue;
    ctx.save();
    trace(ctx, z.shape);
    ctx.clip();
    ctx.strokeStyle = 'rgba(255,255,255,0.13)';
    ctx.lineWidth = 2;
    for (let y = 0; y < COURSE_H + 30; y += 26) {
      ctx.beginPath();
      for (let x = 0; x <= COURSE_W; x += 16) {
        const yy = y + Math.sin(x / 38 + t * 1.6 + y) * 4;
        x ? ctx.lineTo(x, yy) : ctx.moveTo(x, yy);
      }
      ctx.stroke();
    }
    ctx.restore();
  }
  // redraw green patches over the ripples
  for (const z of hole.zones ?? []) if (z.kind === 'green') turf(ctx, z.shape);
  ctx.restore();
}

function drawSlopes(ctx: CanvasRenderingContext2D, hole: HoleDef, now: number): void {
  for (const z of hole.zones ?? []) {
    if (z.kind !== 'slope') continue;
    const len = Math.hypot(z.downhill.x, z.downhill.y);
    const ux = z.downhill.x / len;
    const uy = z.downhill.y / len;
    const shift = ((now / 1000) * 22) % 44;
    ctx.save();
    trace(ctx, z.shape);
    ctx.clip();
    ctx.strokeStyle = 'rgba(255,255,255,0.22)';
    ctx.lineWidth = 3;
    ctx.lineCap = 'round';
    for (let a = -1200; a < 1200; a += 44) {
      for (let b = -1200; b < 1200; b += 40) {
        const cx = COURSE_W / 2 + ux * (a + shift) - uy * b;
        const cy = COURSE_H / 2 + uy * (a + shift) + ux * b;
        if (cx < -20 || cy < -20 || cx > COURSE_W + 20 || cy > COURSE_H + 20) continue;
        ctx.beginPath();
        ctx.moveTo(cx - ux * 6 - uy * 9, cy - uy * 6 + ux * 9);
        ctx.lineTo(cx, cy);
        ctx.lineTo(cx - ux * 6 + uy * 9, cy - uy * 6 - ux * 9);
        ctx.stroke();
      }
    }
    ctx.restore();
  }
}

/** Paint one frame, in course coordinates (the caller has scaled the context). */
export function drawFrame(ctx: CanvasRenderingContext2D, f: FrameInput): void {
  const { round } = f;
  const hole = round.hole;
  ctx.drawImage(f.course, 0, 0, COURSE_W, COURSE_H);
  drawWater(ctx, hole, f.now);
  drawSlopes(ctx, hole, f.now);
  // the cup was painted with the course; redraw it if a green patch covered it
  if ((hole.zones ?? []).some((z) => z.kind === 'green')) {
    ctx.fillStyle = '#05100a';
    ctx.beginPath();
    ctx.arc(hole.cup.x, hole.cup.y, CUP_R, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = 'rgba(255,255,255,0.55)';
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }

  // bumpers
  (hole.bumpers ?? []).forEach((b, i) => {
    const glow = f.effects.flashes.get(i) ?? 0;
    ctx.save();
    ctx.shadowColor = 'rgba(0,0,0,0.35)';
    ctx.shadowBlur = 6;
    ctx.shadowOffsetY = 3;
    const g = ctx.createRadialGradient(b.at.x - b.r * 0.35, b.at.y - b.r * 0.35, 1, b.at.x, b.at.y, b.r);
    g.addColorStop(0, glow ? '#fff7d6' : '#ff8a80');
    g.addColorStop(1, glow ? '#ffd23f' : C.bumper);
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(b.at.x, b.at.y, b.r, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
    ctx.strokeStyle = 'rgba(255,255,255,0.85)';
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.arc(b.at.x, b.at.y, b.r - 5, 0, Math.PI * 2);
    ctx.stroke();
  });

  // spinners, positioned between the last two steps like the ball
  for (const sp of round.world.spinners) {
    const tick = round.tick - 1 + f.alpha;
    ctx.save();
    ctx.shadowColor = 'rgba(0,0,0,0.35)';
    ctx.shadowBlur = 6;
    ctx.shadowOffsetY = 4;
    ctx.strokeStyle = C.blade;
    ctx.lineCap = 'round';
    ctx.lineWidth = BLADE.halfWidth * 2 + 2;
    for (const [a, b] of blades(sp, tick)) {
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    }
    ctx.restore();
    ctx.fillStyle = '#c98b4f';
    ctx.beginPath();
    ctx.arc(sp.pivot.x, sp.pivot.y, BLADE.hub, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = '#6b4322';
    ctx.beginPath();
    ctx.arc(sp.pivot.x, sp.pivot.y, 3, 0, Math.PI * 2);
    ctx.fill();
  }

  const holed = round.phase === 'holed' && round.ball.x === hole.cup.x && round.ball.y === hole.cup.y;
  const bx = f.prev.x + (round.ball.x - f.prev.x) * f.alpha;
  const by = f.prev.y + (round.ball.y - f.prev.y) * f.alpha;

  // trail
  if (f.trail.length > 1) {
    ctx.lineCap = 'round';
    for (let i = 1; i < f.trail.length; i++) {
      ctx.strokeStyle = `rgba(255,255,255,${(0.28 * i) / f.trail.length})`;
      ctx.lineWidth = (BALL_R * 1.2 * i) / f.trail.length;
      ctx.beginPath();
      ctx.moveTo(f.trail[i - 1]!.x, f.trail[i - 1]!.y);
      ctx.lineTo(f.trail[i]!.x, f.trail[i]!.y);
      ctx.stroke();
    }
  }

  // aim line
  if (f.showAim && round.phase === 'aiming') {
    const { angle, power } = round.aim;
    const a = (angle * Math.PI) / 180;
    const length = 30 + power * 1.7;
    const hue = 120 - power * 1.2; // green → red
    ctx.save();
    ctx.strokeStyle = `hsl(${hue} 90% 60%)`;
    ctx.fillStyle = ctx.strokeStyle;
    ctx.lineWidth = 3;
    ctx.setLineDash([2, 7]);
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(bx + Math.cos(a) * (BALL_R + 6), by + Math.sin(a) * (BALL_R + 6));
    const ex = bx + Math.cos(a) * length;
    const ey = by + Math.sin(a) * length;
    ctx.lineTo(ex, ey);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(ex + Math.cos(a) * 9, ey + Math.sin(a) * 9);
    ctx.lineTo(ex + Math.cos(a + 2.5) * 8, ey + Math.sin(a + 2.5) * 8);
    ctx.lineTo(ex + Math.cos(a - 2.5) * 8, ey + Math.sin(a - 2.5) * 8);
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  }

  // ball
  if (!holed) {
    ctx.fillStyle = 'rgba(0,0,0,0.3)';
    ctx.beginPath();
    ctx.ellipse(bx + 2, by + 3, BALL_R, BALL_R * 0.8, 0, 0, Math.PI * 2);
    ctx.fill();
    const g = ctx.createRadialGradient(bx - 2.5, by - 2.5, 1, bx, by, BALL_R);
    g.addColorStop(0, '#ffffff');
    g.addColorStop(1, '#cfd6dd');
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(bx, by, BALL_R, 0, Math.PI * 2);
    ctx.fill();
  }

  // flag: lifts away as the ball comes close
  const near = Math.hypot(bx - hole.cup.x, by - hole.cup.y);
  const lift = holed ? 1 : Math.max(0, Math.min(1, (90 - near) / 60));
  ctx.save();
  ctx.globalAlpha = 1 - lift * 0.75;
  ctx.translate(0, -lift * 14);
  ctx.strokeStyle = '#f4f4f4';
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(hole.cup.x, hole.cup.y);
  ctx.lineTo(hole.cup.x, hole.cup.y - 52);
  ctx.stroke();
  const wave = Math.sin(f.now / 260) * 3;
  ctx.fillStyle = C.flag;
  ctx.beginPath();
  ctx.moveTo(hole.cup.x, hole.cup.y - 52);
  ctx.quadraticCurveTo(hole.cup.x + 14, hole.cup.y - 48 + wave, hole.cup.x + 28, hole.cup.y - 44 + wave);
  ctx.quadraticCurveTo(hole.cup.x + 14, hole.cup.y - 38 - wave, hole.cup.x, hole.cup.y - 34);
  ctx.closePath();
  ctx.fill();
  ctx.restore();

  f.effects.draw(ctx, f.now);
}

export const FRAME_STEP = STEP;
