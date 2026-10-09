"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";
import { buildWaterfall, historyPoints, quantile, type Award, type CurvePoint, type LandedBreakdown } from "@/lib/charts";
import { FEATURE_LABELS, money, num, pct } from "@/lib/format";

const C = {
  primary: "hsl(var(--chart-1))",
  green: "hsl(var(--chart-2))",
  amber: "hsl(var(--chart-3))",
  red: "hsl(var(--chart-4))",
  violet: "hsl(var(--chart-5))",
  muted: "hsl(var(--muted-foreground))",
  grid: "hsl(var(--border))",
};
const AXIS = { fontSize: 11, fill: "hsl(var(--muted-foreground))" };
const TT = { contentStyle: { fontSize: 12, borderRadius: 6, borderColor: "hsl(var(--border))" } };

export function HistoryChart({
  awards,
  levels,
  prices,
  quantity,
}: {
  awards: Award[];
  levels?: number[];
  prices?: number[];
  quantity: number;
}) {
  const pts = historyPoints(awards);
  const q10 = levels && prices ? quantile(levels, prices, 0.1) : null;
  const q50 = levels && prices ? quantile(levels, prices, 0.5) : null;
  const q90 = levels && prices ? quantile(levels, prices, 0.9) : null;
  if (!pts.length) {
    return <div className="flex h-56 items-center justify-center text-xs text-muted-foreground">No past awards on file for this NSN.</div>;
  }
  const now = Date.now();
  const xMin = pts[0].x;
  return (
    <ResponsiveContainer width="100%" height={240}>
      <ScatterChart margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
        <CartesianGrid stroke={C.grid} strokeDasharray="3 3" />
        <XAxis
          type="number"
          dataKey="x"
          domain={[xMin, now]}
          tickFormatter={(t) => {
            const d = new Date(t);
            return `${d.toLocaleDateString("en-US", { month: "short" })} '${String(d.getFullYear()).slice(2)}`;
          }}
          tick={AXIS}
          scale="time"
        />
        <YAxis type="number" dataKey="y" tickFormatter={(v) => money(v, { digits: 2 })} tick={AXIS} width={64} />
        <ZAxis type="number" dataKey="z" range={[25, 400]} name="Quantity" />
        {q10 !== null && q90 !== null && (
          <ReferenceArea x1={now - 1000 * 86400 * 20} x2={now} y1={q10} y2={q90} fill={C.primary} fillOpacity={0.12} ifOverflow="extendDomain" />
        )}
        {q50 !== null && (
          <ReferenceLine y={q50} stroke={C.primary} strokeDasharray="4 3" label={{ value: `predicted median ${money(q50)}`, position: "insideTopLeft", fontSize: 10, fill: C.primary }} />
        )}
        <Tooltip
          {...TT}
          cursor={{ strokeDasharray: "3 3" }}
          formatter={(v: number, name: string) => (name === "Quantity" ? num(v) : name === "x" ? "" : money(v))}
          labelFormatter={() => ""}
          content={({ payload }) => {
            const p = payload?.[0]?.payload as { date: string; y: number; z: number; cage: string } | undefined;
            if (!p) return null;
            return (
              <div className="rounded-md border bg-card px-2 py-1 text-xs shadow">
                <div className="font-medium">{p.date}</div>
                <div>
                  {money(p.y)} × {num(p.z)} · CAGE {p.cage}
                </div>
              </div>
            );
          }}
        />
        <Scatter isAnimationActive={false} data={pts} fill={C.primary} fillOpacity={0.65} name="Past awards" />
        <Legend wrapperStyle={{ fontSize: 11 }} payload={[{ value: `Past awards (bubble = quantity; this RFQ: ${num(quantity)})`, type: "circle", color: C.primary }]} />
      </ScatterChart>
    </ResponsiveContainer>
  );
}

export function PWinChart({
  curve,
  landed,
  recommended,
  conservative,
  aggressive,
}: {
  curve: CurvePoint[];
  landed: number | null;
  recommended: number | null;
  conservative: number | null;
  aggressive: number | null;
}) {
  if (!curve?.length) return <div className="flex h-56 items-center justify-center text-xs text-muted-foreground">No curve available.</div>;
  const marks: { x: number | null; label: string; color: string }[] = [
    { x: landed, label: "landed", color: C.red },
    { x: conservative, label: "conservative", color: C.green },
    { x: recommended, label: "recommended", color: C.primary },
    { x: aggressive, label: "aggressive", color: C.amber },
  ];
  return (
    <ResponsiveContainer width="100%" height={240}>
      <ComposedChart data={curve} margin={{ top: 16, right: 8, bottom: 4, left: 4 }}>
        <CartesianGrid stroke={C.grid} strokeDasharray="3 3" />
        <XAxis dataKey="bid" type="number" domain={["dataMin", "dataMax"]} tickFormatter={(v) => money(v, { digits: 2 })} tick={AXIS} />
        <YAxis yAxisId="p" domain={[0, 1]} tickFormatter={(v) => pct(v)} tick={AXIS} width={40} />
        <YAxis yAxisId="e" orientation="right" tickFormatter={(v) => money(v, { compact: true, digits: 0 })} tick={AXIS} width={56} />
        <Tooltip
          {...TT}
          labelFormatter={(v) => `Bid ${money(Number(v))}`}
          formatter={(v: number, name: string) => (name === "P(win)" ? pct(v, 1) : money(v, { digits: 0 }))}
        />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        <Line isAnimationActive={false} yAxisId="p" type="monotone" dataKey="p_win" name="P(win)" stroke={C.primary} dot={false} strokeWidth={2} />
        <Line isAnimationActive={false} yAxisId="e" type="monotone" dataKey="expected_profit" name="Expected profit" stroke={C.green} dot={false} strokeWidth={1.5} strokeDasharray="5 3" />
        {marks.map((m) =>
          m.x ? (
            <ReferenceLine
              key={m.label}
              yAxisId="p"
              x={m.x}
              stroke={m.color}
              strokeDasharray={m.label === "recommended" ? undefined : "3 3"}
              strokeWidth={m.label === "recommended" ? 2 : 1}
              label={{ value: m.label, position: "top", fontSize: 10, fill: m.color }}
              ifOverflow="extendDomain"
            />
          ) : null,
        )}
      </ComposedChart>
    </ResponsiveContainer>
  );
}

export function WaterfallChart({ breakdown }: { breakdown: LandedBreakdown | null | undefined }) {
  const steps = buildWaterfall(breakdown);
  if (!steps.length) return null;
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={steps} margin={{ top: 8, right: 8, bottom: 4, left: 4 }}>
        <CartesianGrid stroke={C.grid} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="name" tick={{ ...AXIS, fontSize: 10 }} interval={0} />
        <YAxis tickFormatter={(v) => money(v, { digits: 2 })} tick={AXIS} width={64} />
        <Tooltip {...TT} formatter={(v: number, n: string) => (n === "base" ? null : money(v))} labelFormatter={(l) => String(l)} />
        <Bar dataKey="base" stackId="w" fill="transparent" isAnimationActive={false} />
        <Bar isAnimationActive={false} dataKey="value" stackId="w" name="Cost per unit" radius={[3, 3, 0, 0]}>
          {steps.map((s, i) => (
            <Cell key={i} fill={s.total ? C.red : i === 0 ? C.primary : C.amber} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

export function Contributions({ items }: { items: { feature: string; value: unknown; effect_pct: number }[] }) {
  if (!items?.length) return <p className="text-xs text-muted-foreground">No feature explanation (heuristic or cold-start prediction).</p>;
  const max = Math.max(...items.map((i) => Math.abs(i.effect_pct)), 1);
  return (
    <ul className="space-y-1.5">
      {items.map((i) => (
        <li key={i.feature} className="grid grid-cols-[150px_1fr_56px] items-center gap-2 text-xs">
          <span className="truncate" title={`${i.feature} = ${String(i.value ?? "n/a")}`}>
            {FEATURE_LABELS[i.feature] ?? i.feature}
            <span className="ml-1 text-[10px] text-muted-foreground">{formatValue(i.value)}</span>
          </span>
          <div className="relative h-2 rounded bg-muted">
            <div
              className="absolute top-0 h-2 rounded"
              style={{
                left: i.effect_pct < 0 ? `${50 - (Math.abs(i.effect_pct) / max) * 50}%` : "50%",
                width: `${(Math.abs(i.effect_pct) / max) * 50}%`,
                background: i.effect_pct < 0 ? C.green : C.red,
              }}
            />
            <div className="absolute left-1/2 top-[-2px] h-3 w-px bg-border" />
          </div>
          <span className={i.effect_pct < 0 ? "num text-right text-success" : "num text-right text-destructive"}>
            {i.effect_pct > 0 ? "+" : ""}
            {i.effect_pct.toFixed(1)}%
          </span>
        </li>
      ))}
    </ul>
  );
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "number") return Math.abs(v) >= 100 ? num(v) : v.toFixed(2);
  return String(v);
}
