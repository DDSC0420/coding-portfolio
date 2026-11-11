// The physics, checked against what the equations say should happen, set up by hand.
import { describe, expect, test } from 'vitest';
import { BALL_R, CUP_R, FRICTION, MAX_SPEED, STEP, compile, step, type Ball, type Contact, type Outcome } from '../../src/engine/physics.ts';
import type { HoleDef, Polygon } from '../../src/engine/types.ts';

const rect = (x0: number, y0: number, x1: number, y1: number): Polygon => [
  { x: x0, y: y0 }, { x: x1, y: y0 }, { x: x1, y: y1 }, { x: x0, y: y1 },
];

// A big empty box. The cup is put off the course so it can't interfere; cup tests place their own.
function box(extra: Partial<HoleDef> = {}): HoleDef {
  return { name: 'test', par: 3, tee: { x: 100, y: 300 }, cup: { x: -100, y: -100 }, boundary: rect(0, 0, 960, 600), solution: [], ...extra };
}

function roll(hole: HoleDef, ball: Ball, maxSteps = 240 * 40, contacts?: Contact[]): { outcome: Outcome; steps: number; ball: Ball } {
  const world = compile(hole);
  for (let i = 0; i < maxSteps; i++) {
    const outcome = step(world, ball, i, contacts);
    if (outcome !== 'rolling') return { outcome, steps: i + 1, ball };
  }
  return { outcome: 'rolling', steps: maxSteps, ball };
}

// Stopping distance for dv/dt = -a - k·v from speed v0 (solved by hand; DRAG k = 0.35 /s).
function stoppingDistance(v0: number, a: number, k = 0.35): number {
  const c = a / k;
  const T = Math.log((v0 + c) / c) / k;
  return ((v0 + c) * (1 - Math.exp(-k * T))) / k - c * T;
}

describe('rolling', () => {
  test('on the green, a ball stops where the friction equation says (within 1%)', () => {
    for (const v0 of [200, 400, 600]) {
      const r = roll(box({ boundary: rect(0, 0, 4000, 600) }), { x: 10 + BALL_R, y: 300, vx: v0, vy: 0 });
      expect(r.outcome).toBe('stopped');
      const travelled = r.ball.x - (10 + BALL_R);
      expect(Math.abs(travelled - stoppingDistance(v0, FRICTION.green)) / stoppingDistance(v0, FRICTION.green)).toBeLessThan(0.01);
    }
  });

  test('sand stops it far sooner; ice lets it run far further', () => {
    const start = { x: 20, y: 300, vx: 400, vy: 0 };
    const green = roll(box({ boundary: rect(0, 0, 4000, 600) }), { ...start }).ball.x - 20;
    const sand = roll(box({ boundary: rect(0, 0, 4000, 600), zones: [{ kind: 'sand', shape: rect(0, 0, 4000, 600) }] }), { ...start }).ball.x - 20;
    const ice = roll(box({ boundary: rect(0, 0, 4000, 600), zones: [{ kind: 'ice', shape: rect(0, 0, 4000, 600) }] }), { ...start }).ball.x - 20;
    expect(sand).toBeLessThan(green / 3);
    expect(ice).toBeGreaterThan(green * 2);
    expect(Math.abs(sand - stoppingDistance(400, FRICTION.sand)) / stoppingDistance(400, FRICTION.sand)).toBeLessThan(0.02);
  });

  test('speed is capped, so no single step moves the ball more than its radius', () => {
    const ball = { x: 480, y: 300, vx: 5000, vy: 3000 };
    step(compile(box()), ball, 0);
    expect(Math.hypot(ball.vx, ball.vy)).toBeLessThanOrEqual(MAX_SPEED + 1e-9);
    expect(MAX_SPEED * STEP).toBeLessThan(BALL_R);
  });
});

describe('walls', () => {
  test('head-on, the ball comes back at 72% of the speed it hit with', () => {
    const contacts: Contact[] = [];
    const ball = { x: 900, y: 300, vx: 600, vy: 0 };
    const world = compile(box());
    let i = 0;
    while (contacts.length === 0) step(world, ball, i++, contacts);
    expect(ball.vx).toBeLessThan(0);
    expect(Math.abs(ball.vx) / contacts[0]!.speed).toBeCloseTo(0.72, 2);
    expect(ball.x).toBeLessThanOrEqual(960 - BALL_R + 1e-9);
  });

  test('at an angle, it leaves at the same angle (mirror bounce, a little slide lost)', () => {
    const contacts: Contact[] = [];
    const ball = { x: 900, y: 300, vx: 400, vy: 300 };
    const world = compile(box());
    let i = 0;
    while (contacts.length === 0) step(world, ball, i++, contacts);
    const inAngle = Math.atan2(300, 400);
    const outAngle = Math.atan2(ball.vy, -ball.vx);
    // the bounce keeps 96% of the slide and 72% of the push, so it comes off a little steeper
    expect(Math.tan(outAngle) / Math.tan(inAngle)).toBeCloseTo(0.96 / 0.72, 1);
  });

  test('a ball in a corner is pushed out of both walls', () => {
    const r = roll(box(), { x: 955, y: 595, vx: 300, vy: 300 }, 10);
    expect(r.ball.x).toBeLessThanOrEqual(960 - BALL_R + 1e-6);
    expect(r.ball.y).toBeLessThanOrEqual(600 - BALL_R + 1e-6);
  });

  test('no tunnelling: 3,000 full-speed shots at a 1 px wall, from every angle, never get through', () => {
    // The worst case for a step-based simulation: a wall thinner than one step's movement,
    // across the whole course, plus a thin diagonal one in front of it.
    const walls = [rect(300, 0, 301, 600), [{ x: 150, y: 40 }, { x: 151, y: 40 }, { x: 281, y: 560 }, { x: 280, y: 560 }]];
    const world = compile(box({ walls }));
    let seed = 42;
    const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
    let hits = 0;
    let lost = 0;
    let through = 0;
    for (let n = 0; n < 3000; n++) {
      const a = rand() * Math.PI * 2;
      const ball = { x: 10 + rand() * 120, y: 10 + rand() * 580, vx: Math.cos(a) * MAX_SPEED, vy: Math.sin(a) * MAX_SPEED };
      const contacts: Contact[] = [];
      for (let i = 0; i < 480; i++) {
        const outcome = step(world, ball, i, contacts);
        if (outcome === 'lost') lost++;
        if (outcome !== 'rolling') break;
      }
      if (contacts.length) hits++;
      if (ball.x >= 300) through++; // got to the far side of the thin wall
    }
    expect({ lost, through }).toEqual({ lost: 0, through: 0 });
    expect(hits).toBeGreaterThan(2900); // and nearly every shot really did hit something
  });

  test('bouncing off walls never adds speed', () => {
    const world = compile(box({ walls: [rect(400, 200, 560, 400)] }));
    let seed = 7;
    const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
    for (let n = 0; n < 300; n++) {
      const a = rand() * Math.PI * 2;
      const ball = { x: 100 + rand() * 200, y: 100 + rand() * 400, vx: Math.cos(a) * 900, vy: Math.sin(a) * 900 };
      let before = 900;
      for (let i = 0; i < 600; i++) {
        const outcome = step(world, ball, i);
        const now = Math.hypot(ball.vx, ball.vy);
        expect(now).toBeLessThanOrEqual(before + 1e-9);
        before = now;
        if (outcome !== 'rolling') break;
      }
    }
  });
});

describe('the cup', () => {
  const cupHole = (): HoleDef => box({ cup: { x: 500, y: 300 } });

  test('a slow ball rolling over the cup drops in', () => {
    expect(roll(cupHole(), { x: 440, y: 300, vx: 250, vy: 0 }).outcome).toBe('holed');
  });

  test('a fast one skims over the lip and carries on', () => {
    const r = roll(cupHole(), { x: 400, y: 300, vx: 900, vy: 0 }, 240);
    expect(r.ball.x).toBeGreaterThan(500 + CUP_R);
  });

  test('a ball passing just outside the cup is not pulled at all', () => {
    const r = roll(cupHole(), { x: 400, y: 300 - CUP_R - 1, vx: 300, vy: 0 });
    expect(r.outcome).toBe('stopped');
    expect(r.ball.y).toBe(300 - CUP_R - 1);
  });

  test('a fast ball clipping the edge lips out: bent off line, not holed', () => {
    const r = roll(cupHole(), { x: 400, y: 300 - CUP_R + 2, vx: 700, vy: 0 });
    expect(r.outcome).toBe('stopped');
    expect(r.ball.y).not.toBe(300 - CUP_R + 2);
  });
});
describe('ground', () => {
  test('rolling into water reports it', () => {
    const hole = box({ zones: [{ kind: 'water', shape: rect(500, 0, 600, 600) }] });
    expect(roll(hole, { x: 300, y: 300, vx: 500, vy: 0 }).outcome).toBe('water');
  });

  test('a green patch on top of water is safe to cross', () => {
    const hole = box({ zones: [{ kind: 'water', shape: rect(500, 0, 600, 600) }, { kind: 'green', shape: rect(500, 280, 600, 320) }] });
    const r = roll(hole, { x: 300, y: 300, vx: 700, vy: 0 });
    expect(r.outcome).toBe('stopped');
    expect(r.ball.x).toBeGreaterThan(600);
  });

  test('a steep slope rolls a resting ball downhill; a gentle one leaves it be', () => {
    const steep = roll(box({ zones: [{ kind: 'slope', shape: rect(0, 0, 960, 600), downhill: { x: 300, y: 0 } }] }), { x: 200, y: 300, vx: 0, vy: 0 }, 240);
    // net push 300 − 230 = 70 px/s², less drag: x(1 s) = 200·(1 − (1 − e^−0.35)/0.35) ≈ 31.3 px
    expect(steep.ball.x - 200).toBeCloseTo(31.3, 0);
    const gentle = roll(box({ zones: [{ kind: 'slope', shape: rect(0, 0, 960, 600), downhill: { x: FRICTION.slope - 30, y: 0 } }] }), { x: 200, y: 300, vx: 0, vy: 0 });
    expect(gentle.outcome).toBe('stopped');
    expect(gentle.ball.x).toBe(200);
  });

  test('a bumper sends the ball back faster than it arrived', () => {
    const contacts: Contact[] = [];
    const hole = box({ bumpers: [{ at: { x: 500, y: 300 }, r: 20 }] });
    const ball = { x: 400, y: 300, vx: 300, vy: 0 };
    const world = compile(hole);
    let i = 0;
    while (!contacts.some((c) => c.kind === 'bumper') && i < 2400) step(world, ball, i++, contacts);
    expect(ball.vx).toBeLessThan(0);
    expect(Math.abs(ball.vx)).toBeGreaterThan(contacts.find((c) => c.kind === 'bumper')!.speed);
  });
});
