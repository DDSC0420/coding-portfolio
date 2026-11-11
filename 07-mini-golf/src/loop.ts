// Fixed-step game loop: the physics always advances in exact 1/240 s steps, whatever the screen's
// refresh rate, and drawing interpolates between the last two steps so motion stays smooth.

export const STEP_MS = 1000 / 240;
export const MAX_FRAME_MS = 250; // after a stall (tab in the background, debugger), don't try to catch up more than this

/** How many whole steps fit in the time since the last frame, and what's left over. Pure. */
export function advanceClock(carried: number, frameMs: number, stepMs = STEP_MS): { steps: number; carried: number; alpha: number } {
  const available = carried + Math.min(Math.max(frameMs, 0), MAX_FRAME_MS);
  const steps = Math.floor(available / stepMs);
  const left = available - steps * stepMs;
  return { steps, carried: left, alpha: left / stepMs };
}

/** Drive `update` at a fixed rate and `draw` once per screen frame. Returns a stop function. */
export function startLoop(update: () => void, draw: (alpha: number, now: number) => void): () => void {
  let carried = 0;
  let last: number | null = null;
  let handle = 0;
  const frame = (now: number) => {
    const elapsed = last === null ? 0 : now - last;
    last = now;
    const tick = advanceClock(carried, elapsed);
    carried = tick.carried;
    for (let i = 0; i < tick.steps; i++) update();
    draw(tick.alpha, now);
    handle = requestAnimationFrame(frame);
  };
  handle = requestAnimationFrame(frame);
  return () => cancelAnimationFrame(handle);
}
