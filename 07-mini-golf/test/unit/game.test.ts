// The rules of a round and the nine holes themselves.
import { describe, expect, test } from 'vitest';
import { HOLES } from '../../src/engine/holes.ts';
import { MAX_STROKES, Round, angleTo, playShots } from '../../src/engine/game.ts';
import { BALL_R, CUP_R, compile, inside, surfaceAt } from '../../src/engine/physics.ts';
import type { HoleDef, Polygon } from '../../src/engine/types.ts';

const rect = (x0: number, y0: number, x1: number, y1: number): Polygon => [
  { x: x0, y: y0 }, { x: x1, y: y0 }, { x: x1, y: y1 }, { x: x0, y: y1 },
];

describe('every hole', () => {
  test.each(HOLES.map((h, i) => [i + 1, h] as const))('hole %i can be finished in par or better (known shots replayed)', (_n, hole) => {
    const result = playShots(hole, hole.solution);
    expect(result.holed).toBe(true);
    expect(result.strokes).toBeLessThanOrEqual(hole.par);
    expect(result.lost).toBe(0);
  });

  test.each(HOLES.map((h, i) => [i + 1, h] as const))('hole %i is laid out sensibly', (_n, hole) => {
    const world = compile(hole);
    const solid = (x: number, y: number) => (hole.walls ?? []).some((w) => inside(w, x, y));
    for (const spot of [hole.tee, hole.cup]) {
      expect(inside(hole.boundary, spot.x, spot.y)).toBe(true);
      expect(solid(spot.x, spot.y)).toBe(false);
      expect(surfaceAt(world, spot.x, spot.y)?.kind ?? 'green').toBe('green');
    }
    expect(Math.hypot(hole.tee.x - hole.cup.x, hole.tee.y - hole.cup.y)).toBeGreaterThan(200);
    expect(hole.par).toBeGreaterThanOrEqual(2);
  });

  test('nine holes, par 24', () => {
    expect(HOLES).toHaveLength(9);
    expect(HOLES.reduce((a, h) => a + h.par, 0)).toBe(24);
  });
});

describe('the same shots always play out the same way', () => {
  test('replaying a hole gives a bit-for-bit identical result', () => {
    const shots = [{ angle: 33, power: 64 }, { angle: 200, power: 41 }, { angle: 5, power: 77 }];
    for (const hole of HOLES) {
      expect(playShots(hole, shots)).toEqual(playShots(hole, shots));
    }
  });

  test('a snapshot carries on exactly as the original would have', () => {
    const hole = HOLES[5]!; // the windmill: moving parts make timing matter
    const a = new Round([hole]);
    for (let i = 0; i < 137; i++) a.update();
    const saved = a.snapshot();
    a.shoot({ angle: 10, power: 60 });
    while (a.phase === 'rolling') a.update();
    const b = new Round([hole]);
    b.restore(saved);
    b.shoot({ angle: 10, power: 60 });
    while (b.phase === 'rolling') b.update();
    expect(b.ball).toEqual(a.ball);
    expect(b.tick).toBe(a.tick);
  });

  test('on the windmill, when you strike matters', () => {
    const hole = HOLES[5]!;
    const outcomes = new Set<string>();
    for (const wait of [0, 40, 80, 120, 160]) {
      const r = playShots(hole, [{ angle: 0, power: 70, wait }]);
      outcomes.add(`${Math.round(r.ball.x)},${Math.round(r.ball.y)}`);
    }
    expect(outcomes.size).toBeGreaterThan(1);
  });
});

describe('scoring rules', () => {
  const plain: HoleDef = { name: 'plain', par: 2, tee: { x: 100, y: 300 }, cup: { x: 800, y: 300 }, boundary: rect(50, 200, 900, 400), solution: [] };

  test('a stroke is counted for each shot, and the ball is aimed at the cup to start', () => {
    const round = new Round([plain]);
    expect(round.aim).toEqual({ angle: 0, power: 50 });
    round.shoot({ angle: 90, power: 10 });
    expect(round.strokes).toBe(1);
    expect(round.shoot()).toEqual([]); // can't hit a moving ball
    expect(round.strokes).toBe(1);
  });

  test('water costs a penalty stroke and puts the ball back where it was played from', () => {
    const wet: HoleDef = { ...plain, zones: [{ kind: 'water', shape: rect(300, 200, 400, 400) }] };
    const round = new Round([wet]);
    round.shoot({ angle: 0, power: 60 });
    const events = [];
    while (round.phase === 'rolling') events.push(...round.update());
    expect(events.map((e) => e.type)).toContain('water');
    expect(round.strokes).toBe(2);
    expect(round.ball).toEqual({ x: 100, y: 300, vx: 0, vy: 0 });
    expect(round.phase).toBe('aiming');
  });

  test('not down in seven strokes: the hole is picked up and scores seven', () => {
    const round = new Round([plain]);
    for (let i = 0; i < MAX_STROKES; i++) {
      round.shoot({ angle: 180, power: 1 });
      while (round.phase === 'rolling') round.update();
    }
    expect(round.phase).toBe('picked-up');
    expect(round.scores).toEqual([MAX_STROKES]);
  });

  test('after holing out, the next hole starts fresh and the total adds up', () => {
    const round = new Round([HOLES[0]!, HOLES[1]!]);
    round.shoot(HOLES[0]!.solution[0]);
    while (round.phase === 'rolling') round.update();
    expect(round.phase).toBe('holed');
    expect(round.nextHole()).toBe(true);
    expect(round.index).toBe(1);
    expect(round.strokes).toBe(0);
    expect(round.ball).toMatchObject(HOLES[1]!.tee);
    expect(round.total).toBe(1);
    expect(round.parSoFar).toBe(2);
  });

  test('a blade sweeping into a resting ball knocks it on without a stroke', () => {
    const mill: HoleDef = {
      ...plain,
      tee: { x: 400 + 40 + BALL_R + 3.5, y: 300 },
      spinners: [{ pivot: { x: 400, y: 300 }, length: 70, arms: 1, speed: 1.5, phase: -0.5 }],
    };
    const round = new Round([mill]);
    for (let i = 0; i < 240 && round.phase === 'aiming'; i++) round.update();
    expect(round.phase).toBe('rolling');
    expect(round.strokes).toBe(0);
  });

  test('angles are whole degrees measured clockwise from the right', () => {
    expect(angleTo({ x: 0, y: 0 }, { x: 10, y: 0 })).toBe(0);
    expect(angleTo({ x: 0, y: 0 }, { x: 0, y: 10 })).toBe(90);
    expect(angleTo({ x: 0, y: 0 }, { x: -10, y: 0 })).toBe(180);
    expect(angleTo({ x: 0, y: 0 }, { x: 0, y: -10 })).toBe(270);
  });

  test('the cup is big enough for the ball', () => {
    expect(CUP_R).toBeGreaterThan(BALL_R);
  });
});
