/** SVG annular-sector path (angles in radians, 0 = 12 o'clock, clockwise). */
export function arcPath(innerR: number, outerR: number, start: number, end: number): string {
  const sweep = end - start;
  if (sweep <= 0 || outerR <= 0) return "";
  const large = sweep > Math.PI ? 1 : 0;
  const pt = (r: number, a: number) => `${r * Math.sin(a)},${-r * Math.cos(a)}`;
  if (sweep >= 2 * Math.PI - 1e-6) {
    // Full ring: two half arcs.
    const mid = start + Math.PI;
    const outer = `M${pt(outerR, start)}A${outerR},${outerR} 0 1 1 ${pt(outerR, mid)}A${outerR},${outerR} 0 1 1 ${pt(outerR, start)}`;
    if (innerR <= 0) return `${outer}Z`;
    return `${outer}M${pt(innerR, start)}A${innerR},${innerR} 0 1 0 ${pt(innerR, mid)}A${innerR},${innerR} 0 1 0 ${pt(innerR, start)}Z`;
  }
  const outer = `M${pt(outerR, start)}A${outerR},${outerR} 0 ${large} 1 ${pt(outerR, end)}`;
  if (innerR <= 0) return `${outer}L0,0Z`;
  return `${outer}L${pt(innerR, end)}A${innerR},${innerR} 0 ${large} 0 ${pt(innerR, start)}Z`;
}
