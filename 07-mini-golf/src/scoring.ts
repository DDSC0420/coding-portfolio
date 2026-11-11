// Golf words for a score, and the scorecard sums. Pure.

export function scoreName(strokes: number, par: number): string {
  if (strokes === 1) return 'Hole in one!';
  const diff = strokes - par;
  if (diff <= -3) return 'Albatross!';
  if (diff === -2) return 'Eagle!';
  if (diff === -1) return 'Birdie!';
  if (diff === 0) return 'Par';
  if (diff === 1) return 'Bogey';
  if (diff === 2) return 'Double bogey';
  return `${diff} over par`;
}

/** "+3", "−2" or "E" (even), as golfers write it. */
export function toPar(strokes: number, par: number): string {
  const diff = strokes - par;
  if (diff === 0) return 'E';
  return diff > 0 ? `+${diff}` : `−${-diff}`;
}
