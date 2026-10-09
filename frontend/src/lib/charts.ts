/** Chart data builders (pure, unit-tested). */

export interface LandedBreakdown {
  supplier_unit_price?: number;
  effective_unit_price?: number;
  freight_per_unit?: number;
  packaging_per_unit?: number;
  inspection_allowance?: number;
  first_article_allowance_per_unit?: number;
  financing_cost?: number;
  overhead?: number;
  landed?: number;
}

export interface WaterfallStep {
  name: string;
  base: number; // invisible offset
  value: number; // visible bar height
  total: boolean;
}

const STEPS: [keyof LandedBreakdown, string][] = [
  ["freight_per_unit", "Freight"],
  ["packaging_per_unit", "Packaging"],
  ["inspection_allowance", "Inspection"],
  ["first_article_allowance_per_unit", "First article"],
  ["financing_cost", "Financing"],
  ["overhead", "Overhead"],
];

/** Landed-cost waterfall: supplier price, then each add-on stacked on the running total, then the total. */
export function buildWaterfall(b: LandedBreakdown | null | undefined): WaterfallStep[] {
  if (!b) return [];
  const start = b.effective_unit_price ?? b.supplier_unit_price ?? 0;
  const out: WaterfallStep[] = [{ name: "Supplier price", base: 0, value: start, total: false }];
  let running = start;
  for (const [key, name] of STEPS) {
    const v = Number(b[key] ?? 0);
    if (v > 0) {
      out.push({ name, base: running, value: v, total: false });
      running += v;
    }
  }
  out.push({ name: "Landed", base: 0, value: b.landed ?? running, total: true });
  return out;
}

export interface CurvePoint {
  bid: number;
  p_win: number;
  expected_profit: number;
}

/** Highest expected-profit point of a curve (for the chart marker when the server value is missing). */
export function bestPoint(curve: CurvePoint[]): CurvePoint | null {
  return curve.reduce<CurvePoint | null>((best, p) => (!best || p.expected_profit > best.expected_profit ? p : best), null);
}

export interface Award {
  award_date: string;
  quantity: number;
  unit_price: number;
  awardee_cage: string | null;
}

/** Scatter points: x = timestamp, y = price, z = quantity (bubble size). */
export function historyPoints(awards: Award[]): { x: number; y: number; z: number; cage: string; date: string }[] {
  return awards
    .map((a) => ({
      x: new Date(`${a.award_date}T12:00:00`).getTime(),
      y: a.unit_price,
      z: Math.max(a.quantity, 1),
      cage: a.awardee_cage ?? "?",
      date: a.award_date,
    }))
    .filter((p) => Number.isFinite(p.x) && p.y > 0)
    .sort((a, b) => a.x - b.x);
}

/** Quantile lookup from the prediction's levels/prices arrays. */
export function quantile(levels: number[], prices: number[], level: number): number | null {
  const i = levels.findIndex((l) => Math.abs(l - level) < 1e-9);
  return i >= 0 ? prices[i] : null;
}
