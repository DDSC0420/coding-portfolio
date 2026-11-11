// The fixed-step clock, aiming and the golf words.
import { describe, expect, test } from 'vitest';
import { advanceClock, MAX_FRAME_MS, STEP_MS } from '../../src/loop.ts';
import { aimFromDrag, aimFromKey, FULL_POWER_DRAG } from '../../src/aim.ts';
import { scoreName, toPar } from '../../src/scoring.ts';

describe('the clock', () => {
  test('a 60 Hz screen runs four physics steps a frame, carrying the remainder', () => {
    let carried = 0;
    let total = 0;
    for (let i = 0; i < 60; i++) {
      const t = advanceClock(carried, 1000 / 60);
      carried = t.carried;
      total += t.steps;
    }
    expect(total).toBe(240); // exactly 240 steps per simulated second, whatever the frame rate
  });

  test('a 144 Hz screen also gets 240 steps a second, and draws in between', () => {
    let carried = 0;
    let total = 0;
    const alphas = new Set<number>();
    for (let i = 0; i < 144; i++) {
      const t = advanceClock(carried, 1000 / 144);
      carried = t.carried;
      total += t.steps;
      alphas.add(Math.round(t.alpha * 100));
    }
    expect(total).toBeGreaterThanOrEqual(239);
    expect(total).toBeLessThanOrEqual(240);
    expect([...alphas].some((a) => a > 0 && a < 100)).toBe(true); // frames land between steps: drawing interpolates
  });

  test('after a long stall it catches up at most a quarter of a second', () => {
    const t = advanceClock(0, 30_000);
    expect(t.steps).toBe(Math.floor(MAX_FRAME_MS / STEP_MS));
  });
});

describe('aiming', () => {
  const ball = { x: 500, y: 300 };

  test('pulling back left shoots right; the length of the pull is the power', () => {
    expect(aimFromDrag(ball, { x: 500 - FULL_POWER_DRAG / 2, y: 300 })).toEqual({ angle: 0, power: 50 });
    expect(aimFromDrag(ball, { x: 500, y: 300 - FULL_POWER_DRAG })).toEqual({ angle: 90, power: 100 });
    expect(aimFromDrag(ball, { x: 500 + 1000, y: 300 })).toEqual({ angle: 180, power: 100 });
  });

  test('a tiny wobble is not a shot', () => {
    expect(aimFromDrag(ball, { x: 503, y: 302 })).toBeNull();
  });

  test('arrow keys turn by 1° and change power by 1; with Shift, by 10; within limits', () => {
    expect(aimFromKey({ angle: 0, power: 50 }, 'ArrowLeft', false)).toEqual({ angle: 359, power: 50 });
    expect(aimFromKey({ angle: 355, power: 50 }, 'ArrowRight', true)).toEqual({ angle: 5, power: 50 });
    expect(aimFromKey({ angle: 0, power: 95 }, 'ArrowUp', true)).toEqual({ angle: 0, power: 100 });
    expect(aimFromKey({ angle: 0, power: 3 }, 'ArrowDown', true)).toEqual({ angle: 0, power: 1 });
    expect(aimFromKey({ angle: 0, power: 3 }, 'a', false)).toBeNull();
  });
});

describe('golf words', () => {
  test('names a score against par', () => {
    expect(scoreName(1, 3)).toBe('Hole in one!');
    expect(scoreName(2, 4)).toBe('Eagle!');
    expect(scoreName(2, 3)).toBe('Birdie!');
    expect(scoreName(3, 3)).toBe('Par');
    expect(scoreName(4, 3)).toBe('Bogey');
    expect(scoreName(5, 3)).toBe('Double bogey');
    expect(scoreName(7, 3)).toBe('4 over par');
  });

  test('writes a total the way golfers do', () => {
    expect(toPar(24, 24)).toBe('E');
    expect(toPar(27, 24)).toBe('+3');
    expect(toPar(22, 24)).toBe('−2');
  });
});
