// The rules of a round, on top of the physics: strokes, the water penalty, the stroke limit, the
// scorecard. Pure and step-driven like the physics: the screen calls update() once per physics step.

import { bladeTouches, compile, shotVelocity, step, type Ball, type Contact, type World } from './physics.ts';
import type { HoleDef, Shot, Vec } from './types.ts';

export const MAX_STROKES = 7; // not down in 7 and the hole is scored 7 ("picked up")
export const MAX_ROLL_STEPS = 240 * 30; // a single roll is stopped after 30 s, whatever happens

export type Phase = 'aiming' | 'rolling' | 'holed' | 'picked-up' | 'finished';

export type GameEvent =
  | { type: 'shot'; power: number }
  | { type: 'contact'; contact: Contact; x: number; y: number }
  | { type: 'water'; x: number; y: number }
  | { type: 'stopped' }
  | { type: 'holed'; strokes: number; par: number }
  | { type: 'picked-up' };

export interface Aim {
  angle: number; // whole degrees, 0..359
  power: number; // 1..100
}

export interface RoundState {
  ball: Ball;
  lie: Vec;
  strokes: number;
  tick: number;
  phase: Phase;
  aim: Aim;
}

export function angleTo(from: Vec, to: Vec): number {
  const deg = Math.round((Math.atan2(to.y - from.y, to.x - from.x) * 180) / Math.PI);
  return (deg + 360) % 360;
}

export class Round {
  readonly holes: readonly HoleDef[];
  readonly scores: number[] = [];
  index = 0;
  world!: World;
  ball: Ball = { x: 0, y: 0, vx: 0, vy: 0 };
  /** Where the ball was last struck from: it goes back here after water. */
  lie: Vec = { x: 0, y: 0 };
  strokes = 0;
  tick = 0;
  rollSteps = 0;
  phase: Phase = 'aiming';
  aim: Aim = { angle: 0, power: 50 };
  /** Times the ball left the course (a physics failure; the tests require it to stay 0). */
  lostBalls = 0;

  constructor(holes: readonly HoleDef[]) {
    if (holes.length === 0) throw new Error('a round needs at least one hole');
    this.holes = holes;
    this.startHole(0);
  }

  get hole(): HoleDef {
    return this.holes[this.index]!;
  }

  get total(): number {
    return this.scores.reduce((a, b) => a + b, 0);
  }

  get parSoFar(): number {
    return this.holes.slice(0, this.scores.length).reduce((a, h) => a + h.par, 0);
  }

  private startHole(index: number): void {
    this.index = index;
    this.world = compile(this.hole);
    this.ball = { x: this.hole.tee.x, y: this.hole.tee.y, vx: 0, vy: 0 };
    this.lie = { ...this.hole.tee };
    this.strokes = 0;
    this.tick = 0;
    this.phase = 'aiming';
    this.aim = { angle: angleTo(this.hole.tee, this.hole.cup), power: 50 };
  }

  setAim(angle: number, power: number): void {
    this.aim = { angle: ((Math.round(angle) % 360) + 360) % 360, power: Math.max(1, Math.min(100, Math.round(power))) };
  }

  /** Strike the ball with the current aim (or the given shot). Only while aiming. */
  shoot(shot: Shot = this.aim): GameEvent[] {
    if (this.phase !== 'aiming') return [];
    this.setAim(shot.angle, shot.power);
    const { vx, vy } = shotVelocity(this.aim.angle, this.aim.power);
    this.lie = { x: this.ball.x, y: this.ball.y };
    this.ball.vx = vx;
    this.ball.vy = vy;
    this.strokes++;
    this.rollSteps = 0;
    this.phase = 'rolling';
    return [{ type: 'shot', power: this.aim.power }];
  }

  /** One physics step. Moving parts keep moving between shots, so time always advances. */
  update(): GameEvent[] {
    const events: GameEvent[] = [];
    if (this.phase === 'aiming' && this.world.spinners.length && bladeTouches(this.world, this.ball, this.tick + 1)) {
      this.phase = 'rolling'; // knocked on by a blade: rolls again, no stroke
      this.rollSteps = 0;
    }
    if (this.phase !== 'rolling') {
      this.tick++;
      return events;
    }
    const contacts: Contact[] = [];
    const outcome = step(this.world, this.ball, this.tick, contacts);
    this.tick++;
    this.rollSteps++;
    for (const contact of contacts) events.push({ type: 'contact', contact, x: this.ball.x, y: this.ball.y });

    if (outcome === 'holed') {
      this.ball = { x: this.hole.cup.x, y: this.hole.cup.y, vx: 0, vy: 0 };
      this.finishHole(this.strokes);
      events.push({ type: 'holed', strokes: this.strokes, par: this.hole.par });
      return events;
    }
    if (outcome === 'water' || outcome === 'lost') {
      if (outcome === 'water') events.push({ type: 'water', x: this.ball.x, y: this.ball.y });
      else this.lostBalls++;
      // back to where it was played from; water costs a penalty stroke
      if (outcome === 'water') this.strokes++;
      this.ball = { x: this.lie.x, y: this.lie.y, vx: 0, vy: 0 };
      return [...events, ...this.afterRest()];
    }
    if (outcome === 'stopped' || this.rollSteps >= MAX_ROLL_STEPS) {
      this.ball.vx = 0;
      this.ball.vy = 0;
      return [...events, ...this.afterRest()];
    }
    return events;
  }

  private afterRest(): GameEvent[] {
    if (this.strokes >= MAX_STROKES) {
      this.finishHole(MAX_STROKES);
      this.phase = 'picked-up';
      return [{ type: 'picked-up' }];
    }
    this.phase = 'aiming';
    this.aim = { angle: angleTo(this.ball, this.hole.cup), power: this.aim.power };
    return [{ type: 'stopped' }];
  }

  private finishHole(score: number): void {
    this.scores[this.index] = score;
    this.phase = 'holed';
  }

  /** Everything needed to carry on a hole from this moment (for the solver and the tests). */
  snapshot(): RoundState {
    return { ball: { ...this.ball }, lie: { ...this.lie }, strokes: this.strokes, tick: this.tick, phase: this.phase, aim: { ...this.aim } };
  }

  restore(state: RoundState): void {
    this.ball = { ...state.ball };
    this.lie = { ...state.lie };
    this.strokes = state.strokes;
    this.tick = state.tick;
    this.phase = state.phase;
    this.aim = { ...state.aim };
  }

  /** Move on after a hole is done. Returns false when the round is over. */
  nextHole(): boolean {
    if (this.phase !== 'holed' && this.phase !== 'picked-up') return true;
    if (this.index + 1 >= this.holes.length) {
      this.phase = 'finished';
      return false;
    }
    this.startHole(this.index + 1);
    return true;
  }
}

/**
 * Play a list of shots on one hole, as fast as the computer allows. Each shot waits until the
 * ball is at rest (plus its own `wait`, for moving parts). Used by the tests and the solver.
 */
export function playShots(hole: HoleDef, shots: readonly Shot[]): { holed: boolean; strokes: number; ball: Ball; steps: number; lost: number } {
  const round = new Round([hole]);
  const phase = (): Phase => round.phase; // read fresh: shoot() and update() change it
  let steps = 0;
  for (const shot of shots) {
    for (let w = 0; w < (shot.wait ?? 0); w++) round.update();
    if (phase() !== 'aiming') break;
    round.shoot(shot);
    while (phase() === 'rolling') {
      round.update();
      steps++;
    }
    if (phase() === 'holed' || phase() === 'picked-up') break;
  }
  return { holed: round.phase === 'holed', strokes: round.strokes, ball: round.ball, steps, lost: round.lostBalls };
}
