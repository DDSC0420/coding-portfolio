// Turning a mouse or finger drag into a shot: pull back from the ball like a slingshot. Pure.

import type { Vec } from './engine/types.ts';

export const FULL_POWER_DRAG = 170; // course pixels of pull for 100% power
const DEAD_ZONE = 6;

/** The shot a drag describes, or null if the pull is too small to mean anything. */
export function aimFromDrag(ball: Vec, pointer: Vec): { angle: number; power: number } | null {
  const dx = ball.x - pointer.x; // the ball goes the opposite way to the pull
  const dy = ball.y - pointer.y;
  const pull = Math.hypot(dx, dy);
  if (pull < DEAD_ZONE) return null;
  const angle = (Math.round((Math.atan2(dy, dx) * 180) / Math.PI) + 360) % 360;
  const power = Math.max(1, Math.min(100, Math.round((pull / FULL_POWER_DRAG) * 100)));
  return { angle, power };
}

/** A key press's change to the aim: arrows turn and adjust power, Shift makes steps of 10. */
export function aimFromKey(aim: { angle: number; power: number }, key: string, big: boolean): { angle: number; power: number } | null {
  const by = big ? 10 : 1;
  switch (key) {
    case 'ArrowLeft':
      return { angle: (aim.angle - by + 360) % 360, power: aim.power };
    case 'ArrowRight':
      return { angle: (aim.angle + by) % 360, power: aim.power };
    case 'ArrowUp':
      return { angle: aim.angle, power: Math.min(100, aim.power + by) };
    case 'ArrowDown':
      return { angle: aim.angle, power: Math.max(1, aim.power - by) };
    default:
      return null;
  }
}
