// Find a way round each hole in par or better, by search. Dev tool: its output is pasted into
// src/engine/holes.ts as `solution`, and the tests replay those shots.
//
//   node scripts/solve.ts            all holes
//   node scripts/solve.ts 6          just hole 6
//
// Beam search over whole-number shots: try every angle (2° apart) and a spread of powers from
// the best few resting places so far, scored by walking distance to the cup around walls and water.

import { HOLES } from '../src/engine/holes.ts';
import { Round, type RoundState } from '../src/engine/game.ts';
import { inside, surfaceAt, compile, BALL_R } from '../src/engine/physics.ts';
import type { HoleDef, Shot } from '../src/engine/types.ts';

const CELL = 8;

function distanceField(hole: HoleDef): (x: number, y: number) => number {
  const world = compile(hole);
  const cols = Math.ceil(960 / CELL);
  const rows = Math.ceil(600 / CELL);
  const free = new Uint8Array(cols * rows);
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const x = c * CELL + CELL / 2;
      const y = r * CELL + CELL / 2;
      const ok =
        inside(hole.boundary, x, y) &&
        !(hole.walls ?? []).some((w) => inside(w, x, y)) &&
        surfaceAt(world, x, y)?.kind !== 'water';
      free[r * cols + c] = ok ? 1 : 0;
    }
  }
  const dist = new Float64Array(cols * rows).fill(Infinity);
  const start = Math.floor(hole.cup.y / CELL) * cols + Math.floor(hole.cup.x / CELL);
  dist[start] = 0;
  const queue = [start];
  // Dijkstra on a small grid with a plain array (fine for ~9000 cells)
  while (queue.length) {
    let bi = 0;
    for (let i = 1; i < queue.length; i++) if (dist[queue[i]!]! < dist[queue[bi]!]!) bi = i;
    const cur = queue.splice(bi, 1)[0]!;
    const cr = Math.floor(cur / cols);
    const cc = cur % cols;
    for (const [dr, dc, w] of [[0, 1, 1], [1, 0, 1], [0, -1, 1], [-1, 0, 1], [1, 1, 1.414], [1, -1, 1.414], [-1, 1, 1.414], [-1, -1, 1.414]] as const) {
      const nr = cr + dr;
      const nc = cc + dc;
      if (nr < 0 || nc < 0 || nr >= rows || nc >= cols) continue;
      const ni = nr * cols + nc;
      if (!free[ni]) continue;
      const nd = dist[cur]! + w * CELL;
      if (nd < dist[ni]!) {
        if (dist[ni] === Infinity) queue.push(ni);
        dist[ni] = nd;
      }
    }
  }
  return (x, y) => dist[Math.floor(y / CELL) * cols + Math.floor(x / CELL)] ?? Infinity;
}

interface Node {
  shots: Shot[];
  state: RoundState;
  score: number;
}

function solve(hole: HoleDef, maxStrokes: number): Shot[] | null {
  const round = new Round([hole]);
  const field = distanceField(hole);
  const powers = [6, 8, 10, 12, 14, 16, 18, 20, 23, 26, 29, 32, 36, 40, 45, 50, 56, 62, 70, 78, 86, 94, 100];
  const waits = hole.spinners?.length ? [0, 20, 40, 60, 80, 100, 120, 140, 160, 180, 200, 220] : [0];
  let frontier: Node[] = [{ shots: [], state: round.snapshot(), score: field(hole.tee.x, hole.tee.y) }];
  for (let depth = 1; depth <= maxStrokes; depth++) {
    const seen = new Map<string, Node>();
    for (const node of frontier) {
      for (const wait of waits) {
        for (let angle = 0; angle < 360; angle += 2) {
          for (const power of powers) {
            round.restore(node.state);
            for (let w = 0; w < wait; w++) round.update();
            if (round.phase !== 'aiming') continue;
            const shot: Shot = wait ? { angle, power, wait } : { angle, power };
            round.shoot(shot);
            while ((round.phase as string) === 'rolling') round.update();
            if ((round.phase as string) === 'holed' && round.strokes <= maxStrokes) return [...node.shots, shot];
            if ((round.phase as string) !== 'aiming' || round.strokes >= maxStrokes) continue;
            const score = field(round.ball.x, round.ball.y) + round.strokes * 1000;
            const key = `${Math.round(round.ball.x / 12)},${Math.round(round.ball.y / 12)},${round.strokes}`;
            const old = seen.get(key);
            if (!old || score < old.score) seen.set(key, { shots: [...node.shots, shot], state: round.snapshot(), score });
          }
        }
      }
    }
    frontier = [...seen.values()].sort((a, b) => a.score - b.score).slice(0, 6);
    if (!frontier.length) return null;
  }
  return null;
}

const only = process.argv[2] ? [Number(process.argv[2]) - 1] : HOLES.map((_, i) => i);
for (const i of only) {
  const hole = HOLES[i]!;
  const t0 = performance.now();
  let found: Shot[] | null = null;
  for (let strokes = 1; strokes <= hole.par && !found; strokes++) found = solve(hole, strokes);
  const secs = ((performance.now() - t0) / 1000).toFixed(1);
  console.log(`hole ${i + 1} ${hole.name} (par ${hole.par}): ${found ? JSON.stringify(found) : 'NOT FOUND'} in ${secs}s`);
}
