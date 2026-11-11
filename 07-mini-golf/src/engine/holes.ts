// The nine holes. Plain data: shapes in course pixels (960 × 600), par, and a known way round
// (`solution`) that the tests replay to prove every hole can be finished in par or better.

import type { HoleDef, Polygon, Vec } from './types.ts';

const rect = (x0: number, y0: number, x1: number, y1: number): Polygon => [
  { x: x0, y: y0 },
  { x: x1, y: y0 },
  { x: x1, y: y1 },
  { x: x0, y: y1 },
];

const ring = (c: Vec, r: number, n: number, turn = 0): Polygon =>
  Array.from({ length: n }, (_, i) => {
    const a = turn + (i * 2 * Math.PI) / n;
    return { x: Math.round(c.x + r * Math.cos(a)), y: Math.round(c.y + r * Math.sin(a)) };
  });

export const HOLES: readonly HoleDef[] = [
  {
    name: 'Straight in',
    par: 2,
    tee: { x: 200, y: 300 },
    cup: { x: 760, y: 300 },
    boundary: rect(120, 220, 840, 380),
    solution: [{ angle: 0, power: 70 }],
  },
  {
    name: 'Dogleg',
    par: 2,
    tee: { x: 330, y: 420 },
    cup: { x: 730, y: 150 },
    boundary: [
      { x: 260, y: 340 },
      { x: 660, y: 340 },
      { x: 660, y: 80 },
      { x: 800, y: 80 },
      { x: 800, y: 500 },
      { x: 260, y: 500 },
    ],
    solution: [{ angle: 36, power: 100 }],
  },
  {
    name: 'Bunker',
    par: 3,
    tee: { x: 180, y: 300 },
    cup: { x: 760, y: 300 },
    boundary: rect(100, 160, 860, 440),
    walls: [rect(390, 255, 420, 345)],
    zones: [{ kind: 'sand', shape: [
      { x: 560, y: 205 }, { x: 640, y: 205 }, { x: 670, y: 300 }, { x: 640, y: 395 }, { x: 560, y: 395 }, { x: 590, y: 300 },
    ] }],
    solution: [{ angle: 22, power: 94 }],
  },
  {
    name: 'The river',
    par: 3,
    tee: { x: 160, y: 380 },
    cup: { x: 800, y: 200 },
    boundary: rect(80, 120, 880, 480),
    zones: [
      { kind: 'water', shape: rect(420, 120, 540, 480) },
      { kind: 'green', shape: rect(420, 268, 540, 332) }, // the bridge
    ],
    walls: [rect(620, 120, 650, 290)],
    solution: [{ angle: 186, power: 100 }, { angle: 62, power: 78 }],
  },
  {
    name: 'Uphill',
    par: 2,
    tee: { x: 170, y: 300 },
    cup: { x: 790, y: 300 },
    boundary: rect(100, 170, 860, 430),
    zones: [
      { kind: 'slope', shape: rect(360, 170, 620, 430), downhill: { x: -330, y: 0 } },
    ],
    walls: [rect(660, 170, 690, 250), rect(660, 350, 690, 430)],
    solution: [{ angle: 0, power: 86 }],
  },
  {
    name: 'Windmill',
    par: 3,
    tee: { x: 180, y: 300 },
    cup: { x: 780, y: 300 },
    boundary: rect(100, 150, 860, 450),
    walls: [rect(470, 150, 510, 268), rect(470, 332, 510, 450)],
    spinners: [{ pivot: { x: 490, y: 300 }, length: 64, arms: 3, speed: 1.4, phase: 0 }],
    solution: [{ angle: 312, power: 86, wait: 100 }, { angle: 14, power: 50 }],
  },
  {
    name: 'Pinball',
    par: 3,
    tee: { x: 170, y: 450 },
    cup: { x: 790, y: 150 },
    boundary: rect(100, 90, 860, 510),
    bumpers: [
      { at: { x: 360, y: 300 }, r: 22 },
      { at: { x: 480, y: 210 }, r: 22 },
      { at: { x: 480, y: 390 }, r: 22 },
      { at: { x: 600, y: 300 }, r: 22 },
      { at: { x: 700, y: 210 }, r: 18 },
    ],
    walls: [rect(260, 90, 290, 330)],
    solution: [{ angle: 48, power: 100 }],
  },
  {
    name: 'Black ice',
    par: 3,
    tee: { x: 170, y: 160 },
    cup: { x: 170, y: 440 },
    boundary: [
      { x: 100, y: 90 },
      { x: 860, y: 90 },
      { x: 860, y: 510 },
      { x: 100, y: 510 },
      { x: 100, y: 370 },
      { x: 700, y: 370 },
      { x: 700, y: 230 },
      { x: 100, y: 230 },
    ],
    zones: [
      { kind: 'ice', shape: rect(560, 90, 860, 510) },
      { kind: 'sand', shape: rect(300, 400, 360, 510) },
    ],
    solution: [{ angle: 40, power: 100 }, { angle: 140, power: 70 }],
  },
  {
    name: 'Island green',
    par: 3,
    tee: { x: 160, y: 300 },
    cup: { x: 780, y: 350 },
    boundary: rect(80, 80, 880, 520),
    zones: [
      { kind: 'water', shape: rect(260, 80, 880, 520) },
      { kind: 'green', shape: rect(258, 272, 620, 328) },
      { kind: 'green', shape: ring({ x: 730, y: 300 }, 130, 12, Math.PI / 12) },
      { kind: 'slope', shape: rect(420, 272, 520, 328), downhill: { x: 0, y: 260 } },
    ],
    bumpers: [{ at: { x: 705, y: 318 }, r: 16 }, { at: { x: 745, y: 215 }, r: 16 }],
    solution: [{ angle: 356, power: 70 }, { angle: 94, power: 18 }],
  },
];
