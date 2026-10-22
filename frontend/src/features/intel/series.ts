/*
 * Series rules the intelligence pages share: what counts as measured, and how
 * a per-key series fills its missing days.
 */

/** Something was counted: a series with no value above 0 is no data, drawn as an empty state. */
export const measured = (series: { points: { y: number }[] }[]) => series.some((s) => s.points.some((p) => p.y > 0));

/**
 * Densify per-key series: once a key has appeared, each of its missing days (or weeks)
 * in `xs` is a measured 0; before its first point it is absent, never a 0 nobody measured.
 * Keys keep the order they first appear in.
 */
export function densify(points: { x: string; key: string; y: number }[], xs: string[]): Map<string, { x: string; y: number }[]> {
  const byKey = new Map<string, Map<string, number>>();
  for (const p of points) {
    const values = byKey.get(p.key) ?? new Map<string, number>();
    values.set(p.x, (values.get(p.x) ?? 0) + p.y);
    byKey.set(p.key, values);
  }
  const out = new Map<string, { x: string; y: number }[]>();
  for (const [key, values] of byKey) {
    const first = [...values.keys()].sort()[0]!;
    out.set(
      key,
      xs.filter((x) => x >= first).map((x) => ({ x, y: values.get(x) ?? 0 })),
    );
  }
  return out;
}
