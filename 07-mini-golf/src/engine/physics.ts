// The ball and the course: a fixed-step 2D simulation with no DOM, no clock and no randomness.
// The same hole, the same shots and the same step count always give exactly the same result,
// which is what lets the tests replay a round and the game draw it at any frame rate.
//
// One step is 1/240 s. The ball is capped at 1100 px/s, so it moves at most 4.6 px a step, less
// than its 7 px radius: it can sink into a wall's reach but never past the wall's line, so a
// plain closest-point test can't be tunnelled through. Moving blades are slow enough (and given
// in the hole data) that the same holds relative to them.

import type { Bumper, HoleDef, Polygon, Spinner, Vec, Zone } from './types.ts';

export const STEP = 1 / 240;
export const BALL_R = 7;
export const CUP_R = 11;
export const MAX_SPEED = 1100;
export const SPEED_PER_POWER = 10; // power 100 → 1000 px/s
export const STOP_SPEED = 5;

/** Rolling resistance by surface, px/s² (a constant drag, like friction). */
export const FRICTION = { green: 230, slope: 230, sand: 1500, ice: 40 } as const;
const DRAG = 0.35; // per second, proportional to speed: takes the edge off long rolls
const WALL_BOUNCE = 0.72; // share of speed into a wall that comes back out
const WALL_SLIDE = 0.96; // share of speed along a wall kept on impact
const BUMPER_BOUNCE = 1.1;
const BUMPER_MIN_KICK = 260;
const BLADE_BOUNCE = 0.6;
const BLADE_HALF_WIDTH = 3;
const HUB_R = 10;
const CAPTURE_SPEED = 430; // slower than this over the cup and it drops
const CUP_PULL = 2400; // px/s² toward the centre while over the cup (the lip)

export interface Ball {
  x: number;
  y: number;
  vx: number;
  vy: number;
}

interface Segment {
  ax: number;
  ay: number;
  dx: number;
  dy: number;
  len2: number;
  nx: number; // unit normal, used only if the ball's centre lands exactly on the line
  ny: number;
}

interface CompiledZone {
  zone: Zone;
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

export interface World {
  hole: HoleDef;
  segments: Segment[];
  zones: CompiledZone[];
  bumpers: readonly Bumper[];
  spinners: readonly Spinner[];
}

export type Contact = { kind: 'wall' | 'bumper' | 'blade'; speed: number };

/** What happened in one step. */
export type Outcome = 'rolling' | 'stopped' | 'holed' | 'water' | 'lost';

function segment(a: Vec, b: Vec): Segment {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy);
  return { ax: a.x, ay: a.y, dx, dy, len2: dx * dx + dy * dy, nx: -dy / len, ny: dx / len };
}

function edges(poly: Polygon): Segment[] {
  const out: Segment[] = [];
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i]!;
    const b = poly[(i + 1) % poly.length]!;
    if (a.x !== b.x || a.y !== b.y) out.push(segment(a, b));
  }
  return out;
}

export function compile(hole: HoleDef): World {
  const segments = [...edges(hole.boundary), ...(hole.walls ?? []).flatMap(edges)];
  const zones = (hole.zones ?? []).map((zone) => {
    const xs = zone.shape.map((p) => p.x);
    const ys = zone.shape.map((p) => p.y);
    return { zone, minX: Math.min(...xs), minY: Math.min(...ys), maxX: Math.max(...xs), maxY: Math.max(...ys) };
  });
  return { hole, segments, zones, bumpers: hole.bumpers ?? [], spinners: hole.spinners ?? [] };
}

/** Even-odd rule point-in-polygon test. */
export function inside(poly: Polygon, x: number, y: number): boolean {
  let hit = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const a = poly[i]!;
    const b = poly[j]!;
    if (a.y > y !== b.y > y && x < ((b.x - a.x) * (y - a.y)) / (b.y - a.y) + a.x) hit = !hit;
  }
  return hit;
}

/** The zone under a point: the last one listed wins, so zones can be layered. */
export function surfaceAt(world: World, x: number, y: number): Zone | null {
  let found: Zone | null = null;
  for (const z of world.zones) {
    if (x < z.minX || x > z.maxX || y < z.minY || y > z.maxY) continue;
    if (inside(z.zone.shape, x, y)) found = z.zone;
  }
  return found;
}

/** Where each blade of a spinner points at a given step: [pivot, tip] pairs. */
export function blades(spinner: Spinner, tick: number): Array<[Vec, Vec]> {
  const base = spinner.phase + spinner.speed * tick * STEP;
  const out: Array<[Vec, Vec]> = [];
  for (let k = 0; k < spinner.arms; k++) {
    const a = base + (k * 2 * Math.PI) / spinner.arms;
    out.push([spinner.pivot, { x: spinner.pivot.x + spinner.length * Math.cos(a), y: spinner.pivot.y + spinner.length * Math.sin(a) }]);
  }
  return out;
}

/**
 * Push the ball out of a segment's reach and bounce it. The segment may be moving (a blade), in
 * which case the bounce happens relative to the segment's own velocity at the contact point.
 * Returns the speed of the impact, 0 if there was none.
 */
function collideSegment(ball: Ball, s: Segment, reach: number, bounce: number, slide: number, spin?: { px: number; py: number; w: number }): number {
  const px = ball.x - s.ax;
  const py = ball.y - s.ay;
  let t = (px * s.dx + py * s.dy) / s.len2;
  if (t < 0) t = 0;
  else if (t > 1) t = 1;
  const cx = s.ax + t * s.dx;
  const cy = s.ay + t * s.dy;
  let nx = ball.x - cx;
  let ny = ball.y - cy;
  const d2 = nx * nx + ny * ny;
  if (d2 >= reach * reach) return 0;
  let d = Math.sqrt(d2);
  if (d < 1e-9) {
    nx = s.nx;
    ny = s.ny;
    d = 0;
  } else {
    nx /= d;
    ny /= d;
  }
  ball.x += nx * (reach - d);
  ball.y += ny * (reach - d);
  // velocity of the surface at the contact point (zero for a fixed wall)
  const wx = spin ? -spin.w * (cy - spin.py) : 0;
  const wy = spin ? spin.w * (cx - spin.px) : 0;
  const rvx = ball.vx - wx;
  const rvy = ball.vy - wy;
  const vn = rvx * nx + rvy * ny;
  if (vn >= 0) return 0;
  const tx = rvx - vn * nx;
  const ty = rvy - vn * ny;
  const out = -vn * bounce;
  ball.vx = wx + tx * slide + out * nx;
  ball.vy = wy + ty * slide + out * ny;
  return -vn;
}

function collideCircle(ball: Ball, cx: number, cy: number, r: number, bounce: number, minKick: number): number {
  let nx = ball.x - cx;
  let ny = ball.y - cy;
  const reach = r + BALL_R;
  const d2 = nx * nx + ny * ny;
  if (d2 >= reach * reach) return 0;
  const d = Math.sqrt(d2) || 1e-9;
  nx /= d;
  ny /= d;
  ball.x = cx + nx * reach;
  ball.y = cy + ny * reach;
  const vn = ball.vx * nx + ball.vy * ny;
  if (vn >= 0) return 0;
  const out = Math.max(-vn * bounce, minKick);
  ball.vx += (out - vn) * nx;
  ball.vy += (out - vn) * ny;
  return -vn;
}

function capSpeed(ball: Ball): void {
  const s = Math.hypot(ball.vx, ball.vy);
  if (s > MAX_SPEED) {
    ball.vx *= MAX_SPEED / s;
    ball.vy *= MAX_SPEED / s;
  }
}

/** Do any of the hole's blades touch the ball at this step? (A resting ball can be knocked on.) */
export function bladeTouches(world: World, ball: Ball, tick: number): boolean {
  for (const sp of world.spinners) {
    for (const [a, b] of blades(sp, tick)) {
      const s = segment(a, b);
      let t = ((ball.x - s.ax) * s.dx + (ball.y - s.ay) * s.dy) / s.len2;
      t = Math.max(0, Math.min(1, t));
      if (Math.hypot(ball.x - (s.ax + t * s.dx), ball.y - (s.ay + t * s.dy)) < BALL_R + BLADE_HALF_WIDTH) return true;
    }
  }
  return false;
}

/**
 * Advance the ball by one step. `tick` is the hole's step count (it positions moving blades).
 * Contacts are appended to `contacts` if given, for sounds and effects.
 */
export function step(world: World, ball: Ball, tick: number, contacts?: Contact[]): Outcome {
  const zone = surfaceAt(world, ball.x, ball.y);
  if (zone?.kind === 'water') return 'water';
  const friction = zone ? FRICTION[zone.kind] : FRICTION.green;

  // slope pushes, then rolling resistance and drag slow it
  if (zone?.kind === 'slope') {
    ball.vx += zone.downhill.x * STEP;
    ball.vy += zone.downhill.y * STEP;
  }
  let speed = Math.hypot(ball.vx, ball.vy);
  if (speed > 0) {
    const slow = friction * STEP + DRAG * speed * STEP;
    const k = slow >= speed ? 0 : (speed - slow) / speed;
    ball.vx *= k;
    ball.vy *= k;
    speed *= k;
  }

  // the cup: slow enough and well over it, it drops; faster, the lip pulls it round
  const cup = world.hole.cup;
  const toCupX = cup.x - ball.x;
  const toCupY = cup.y - ball.y;
  const cupDist = Math.hypot(toCupX, toCupY);
  if (cupDist < CUP_R) {
    if (speed < CAPTURE_SPEED && cupDist < CUP_R - 2) return 'holed';
    ball.vx += (toCupX / (cupDist || 1)) * CUP_PULL * STEP;
    ball.vy += (toCupY / (cupDist || 1)) * CUP_PULL * STEP;
  }

  capSpeed(ball);
  ball.x += ball.vx * STEP;
  ball.y += ball.vy * STEP;

  // two passes so a ball wedged in a corner is pushed out of both walls
  const next = tick + 1;
  for (let pass = 0; pass < 2; pass++) {
    for (const s of world.segments) {
      const hit = collideSegment(ball, s, BALL_R, WALL_BOUNCE, WALL_SLIDE);
      if (hit && contacts && pass === 0) contacts.push({ kind: 'wall', speed: hit });
    }
    for (const b of world.bumpers) {
      const hit = collideCircle(ball, b.at.x, b.at.y, b.r, BUMPER_BOUNCE, BUMPER_MIN_KICK);
      if (hit && contacts) contacts.push({ kind: 'bumper', speed: hit });
    }
    for (const sp of world.spinners) {
      const spin = { px: sp.pivot.x, py: sp.pivot.y, w: sp.speed };
      for (const [a, b] of blades(sp, next)) {
        const hit = collideSegment(ball, segment(a, b), BALL_R + BLADE_HALF_WIDTH, BLADE_BOUNCE, WALL_SLIDE, spin);
        if (hit && contacts && pass === 0) contacts.push({ kind: 'blade', speed: hit });
      }
      collideCircle(ball, sp.pivot.x, sp.pivot.y, HUB_R, WALL_BOUNCE, 0);
    }
  }
  capSpeed(ball);

  // Safety net: if the ball ever ends up outside the course or inside a block, say so (tests check it never does).
  if (!inside(world.hole.boundary, ball.x, ball.y) || (world.hole.walls ?? []).some((w) => inside(w, ball.x, ball.y))) {
    return 'lost';
  }

  speed = Math.hypot(ball.vx, ball.vy);
  if (speed < STOP_SPEED) {
    const under = surfaceAt(world, ball.x, ball.y);
    const pull = under?.kind === 'slope' ? Math.hypot(under.downhill.x, under.downhill.y) : 0;
    const grip = under && under.kind !== 'water' ? FRICTION[under.kind] : FRICTION.green;
    if (pull <= grip) {
      ball.vx = 0;
      ball.vy = 0;
      return 'stopped';
    }
  }
  return 'rolling';
}

/** The velocity a shot gives the ball. */
export function shotVelocity(angleDeg: number, power: number): { vx: number; vy: number } {
  const a = (angleDeg * Math.PI) / 180;
  const speed = Math.max(1, Math.min(100, power)) * SPEED_PER_POWER;
  return { vx: speed * Math.cos(a), vy: speed * Math.sin(a) };
}

export const BLADE = { halfWidth: BLADE_HALF_WIDTH, hub: HUB_R } as const;
