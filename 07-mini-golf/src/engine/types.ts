// The shapes a hole is made of. Coordinates are course pixels: the course is 960 × 600, x to the
// right, y down. A hole is plain data, so it can be tested, solved and drawn by separate code.

export interface Vec {
  x: number;
  y: number;
}

export type Polygon = readonly Vec[];

/** A patch of ground. Zones are layered in order, so a green patch can sit on top of water. */
export type Zone =
  | { kind: 'green'; shape: Polygon }
  | { kind: 'sand'; shape: Polygon }
  | { kind: 'ice'; shape: Polygon }
  | { kind: 'water'; shape: Polygon }
  | { kind: 'slope'; shape: Polygon; downhill: Vec }; // downhill = acceleration in px/s²

/** A round, springy post that kicks the ball away. */
export interface Bumper {
  at: Vec;
  r: number;
}

/** Windmill-style blades turning about a pivot. Angle at time t is phase + speed·t. */
export interface Spinner {
  pivot: Vec;
  length: number;
  arms: number;
  speed: number; // radians per second (positive turns clockwise on screen)
  phase: number; // radians
}

/** One stroke: a direction and a strength, both whole numbers so a shot can be written down and replayed exactly. */
export interface Shot {
  angle: number; // degrees, 0 = right, 90 = down
  power: number; // 1 to 100
  wait?: number; // physics steps to wait before striking (only matters with moving parts)
}

export interface HoleDef {
  name: string;
  par: number;
  tee: Vec;
  cup: Vec;
  boundary: Polygon; // the outer wall: the ball stays inside it
  walls?: readonly Polygon[]; // solid blocks: the ball stays outside them
  zones?: readonly Zone[];
  bumpers?: readonly Bumper[];
  spinners?: readonly Spinner[];
  /** A way round in par or better, found by scripts/solve.ts and replayed by the tests. */
  solution: readonly Shot[];
}
