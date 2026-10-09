"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BrainCircuit, CloudDownload, MailCheck, Tags } from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Empty, ErrorState, Loading, PageHeader, Stat } from "@/components/common";
import { FormNotice } from "@/components/form";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/misc";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/overlay";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, errorMessage, unwrap, type Schemas } from "@/lib/api/client";
import { useMe } from "@/lib/hooks";
import { formatDateTime, formatNsn, money, num, pct } from "@/lib/format";

type ModelRun = Schemas["ModelRunOut"];
const AXIS = { fontSize: 11, fill: "hsl(var(--muted-foreground))" };
const COLORS = ["hsl(var(--chart-1))", "hsl(var(--chart-3))", "hsl(var(--muted-foreground))", "hsl(var(--chart-2))"];

type M = {
  pinball_mean?: number;
  model?: Record<string, unknown>;
  baseline?: Record<string, unknown>;
  uncalibrated?: Record<string, unknown>;
  pwin_calibration?: { bins: { bin: string; predicted: number; observed: number; count: number }[]; brier: number; ece: number };
  baseline_pwin_calibration?: { bins: { predicted: number; observed: number }[] };
  backtest?: { assumption: string; policies: Record<string, { bids: number; wins: number; win_rate: number; total_profit: number; profit_per_bid: number }> };
  rows?: { train: number; calib: number; test: number };
  feature_importance?: Record<string, number>;
  promote_reason?: string;
  windows?: Record<string, unknown>;
};

function Triggers() {
  const qc = useQueryClient();
  const [msg, setMsg] = useState<string | null>(null);
  const trigger = useMutation({
    mutationFn: async (kind: "ingest" | "labels" | "train" | "followups") => {
      const path = `/api/admin/${kind}` as "/api/admin/ingest";
      return unwrap(await api.POST(path));
    },
    onSuccess: (r) => {
      setMsg(`Queued ${r.queued} (job ${r.job_id}). Refresh in a minute.`);
      setTimeout(() => qc.invalidateQueries(), 4000);
    },
    onError: (e) => setMsg(errorMessage(e)),
  });
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button size="sm" onClick={() => trigger.mutate("ingest")} disabled={trigger.isPending}>
        <CloudDownload /> Refresh RFQs now
      </Button>
      <Button size="sm" variant="outline" onClick={() => trigger.mutate("labels")} disabled={trigger.isPending}>
        <Tags /> Pull awards
      </Button>
      <Button size="sm" variant="outline" onClick={() => trigger.mutate("train")} disabled={trigger.isPending}>
        <BrainCircuit /> Retrain model
      </Button>
      <Button size="sm" variant="outline" onClick={() => trigger.mutate("followups")} disabled={trigger.isPending}>
        <MailCheck /> Run follow-ups
      </Button>
      {msg && <FormNotice message={msg} />}
    </div>
  );
}

function IngestionRuns() {
  const q = useQuery({ queryKey: ["admin", "runs"], queryFn: async () => unwrap(await api.GET("/api/admin/ingestion-runs")) });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorState error={q.error} />;
  if (!q.data?.length) return <Empty title="No ingestion runs yet">Run <code>make ingest</code> or use the button above.</Empty>;
  return (
    <div className="overflow-hidden rounded-lg border bg-card">
      <Table>
        <THead>
          <tr>
            <TH>Started</TH>
            <TH>Source</TH>
            <TH>Status</TH>
            <TH className="text-right">Rows</TH>
            <TH>Details</TH>
            <TH>Errors</TH>
          </tr>
        </THead>
        <TBody>
          {q.data.map((r) => (
            <TR key={r.id}>
              <TD className="text-[12px]">{formatDateTime(r.started_at)}</TD>
              <TD className="font-mono text-[12px]">{r.source}</TD>
              <TD>
                <Badge variant={r.status === "success" ? "success" : r.status === "partial" ? "warning" : r.status === "running" ? "secondary" : "destructive"}>{r.status}</Badge>
              </TD>
              <TD className="num text-right">{num(r.rows_ingested)}</TD>
              <TD className="max-w-[380px] truncate text-[11px] text-muted-foreground" title={JSON.stringify(r.details)}>
                {Object.entries(r.details)
                  .filter(([, v]) => typeof v === "number" || typeof v === "string")
                  .map(([k, v]) => `${k}: ${v}`)
                  .join(" · ")}
              </TD>
              <TD>
                {r.errors.length ? (
                  <details className="text-[11px]">
                    <summary className="cursor-pointer text-destructive">{r.errors.length} error(s)</summary>
                    <pre className="max-h-40 max-w-[420px] overflow-auto whitespace-pre-wrap">{JSON.stringify(r.errors.slice(0, 20), null, 1)}</pre>
                  </details>
                ) : (
                  <span className="text-muted-foreground">—</span>
                )}
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>
    </div>
  );
}

function ModelDetail({ run }: { run: ModelRun }) {
  const m = run.metrics as M;
  const model = (m.model ?? {}) as Record<string, number | Record<string, number>>;
  const base = (m.baseline ?? {}) as Record<string, number | Record<string, number>>;
  const raw = (m.uncalibrated ?? {}) as Record<string, number | Record<string, number>>;
  const qc = (model.quantile_coverage ?? {}) as Record<string, number>;
  const coverage = Object.keys(qc).map((k) => ({
    level: Number(k),
    nominal: Number(k),
    model: qc[k],
    uncalibrated: ((raw.quantile_coverage ?? {}) as Record<string, number>)[k],
    baseline: ((base.quantile_coverage ?? {}) as Record<string, number>)[k],
  }));
  const calib = (m.pwin_calibration?.bins ?? []).map((b) => ({ ...b, perfect: b.predicted }));
  const bt = Object.entries(m.backtest?.policies ?? {}).map(([name, v]) => ({ name, ...v }));
  const rows: [string, (x: Record<string, unknown>) => string][] = [
    ["Mean pinball loss (primary)", (x) => (typeof x.pinball_mean === "number" ? x.pinball_mean.toFixed(4) : "—")],
    ["MAPE of median", (x) => pct(x.mape_median as number, 1)],
    ["Median abs. % error", (x) => pct(x.median_abs_pct_error as number, 1)],
    ["80% interval coverage", (x) => pct(x.coverage_80 as number, 1)],
    ["90% interval coverage", (x) => pct(x.coverage_90 as number, 1)],
  ];
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>Accuracy on the test window</CardTitle>
          <CardDescription>
            Train {run.train_window} · test {run.test_window} · rows {num(m.rows?.train)} / {num(m.rows?.calib)} / {num(m.rows?.test)}
          </CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <THead>
              <tr>
                <TH>Metric</TH>
                <TH className="text-right">LightGBM + conformal</TH>
                <TH className="text-right">Uncalibrated</TH>
                <TH className="text-right">Baseline</TH>
              </tr>
            </THead>
            <TBody>
              {rows.map(([label, f]) => (
                <TR key={label}>
                  <TD>{label}</TD>
                  <TD className="num text-right font-semibold">{f(model)}</TD>
                  <TD className="num text-right">{f(raw)}</TD>
                  <TD className="num text-right">{f(base)}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
          <p className="px-4 py-2 text-[11px] text-muted-foreground">{m.promote_reason ?? run.notes}</p>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Quantile coverage</CardTitle>
          <CardDescription>Empirical P(W ≤ q) vs nominal level. On the diagonal = calibrated.</CardDescription>
        </CardHeader>
        <CardContent>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={coverage}>
              <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="3 3" />
              <XAxis dataKey="level" tick={AXIS} />
              <YAxis domain={[0, 1]} tick={AXIS} tickFormatter={(v) => pct(v)} width={40} />
              <Tooltip formatter={(v: number) => pct(v, 1)} contentStyle={{ fontSize: 12 }} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Line isAnimationActive={false} dataKey="nominal" stroke={COLORS[2]} strokeDasharray="4 4" dot={false} />
              <Line isAnimationActive={false} dataKey="model" stroke={COLORS[0]} />
              <Line isAnimationActive={false} dataKey="uncalibrated" stroke={COLORS[1]} />
              <Line isAnimationActive={false} dataKey="baseline" stroke={COLORS[3]} strokeDasharray="2 2" />
            </LineChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>P(win) calibration</CardTitle>
          <CardDescription>
            Predicted P(W &gt; bid) vs observed frequency · Brier {m.pwin_calibration?.brier?.toFixed(4)} · ECE {m.pwin_calibration?.ece?.toFixed(4)}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={calib}>
              <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="3 3" />
              <XAxis dataKey="predicted" type="number" domain={[0, 1]} tick={AXIS} tickFormatter={(v) => pct(v)} />
              <YAxis domain={[0, 1]} tick={AXIS} tickFormatter={(v) => pct(v)} width={40} />
              <Tooltip formatter={(v: number) => pct(v, 1)} labelFormatter={(v) => `predicted ${pct(Number(v), 1)}`} contentStyle={{ fontSize: 12 }} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Line isAnimationActive={false} dataKey="perfect" stroke={COLORS[2]} strokeDasharray="4 4" dot={false} name="perfect" />
              <Line isAnimationActive={false} dataKey="observed" stroke={COLORS[0]} name="observed" />
            </LineChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Bidding backtest</CardTitle>
          <CardDescription>{m.backtest?.assumption}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          <ResponsiveContainer width="100%" height={160}>
            <BarChart data={bt}>
              <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="name" tick={AXIS} />
              <YAxis tick={AXIS} tickFormatter={(v) => money(v, { compact: true, digits: 0 })} width={56} />
              <Tooltip formatter={(v: number) => money(v, { digits: 0 })} contentStyle={{ fontSize: 12 }} />
              <Bar isAnimationActive={false} dataKey="total_profit" name="Total profit" fill={COLORS[0]} />
            </BarChart>
          </ResponsiveContainer>
          <Table>
            <THead>
              <tr>
                <TH>Policy</TH>
                <TH className="text-right">Bids</TH>
                <TH className="text-right">Win rate</TH>
                <TH className="text-right">Total profit</TH>
                <TH className="text-right">Profit / bid</TH>
              </tr>
            </THead>
            <TBody>
              {bt.map((p) => (
                <TR key={p.name}>
                  <TD className="font-mono text-[12px]">{p.name}</TD>
                  <TD className="num text-right">{num(p.bids)}</TD>
                  <TD className="num text-right">{pct(p.win_rate)}</TD>
                  <TD className="num text-right">{money(p.total_profit, { digits: 0 })}</TD>
                  <TD className="num text-right">{money(p.profit_per_bid)}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
          <p className="text-[11px] text-muted-foreground">
            Under this assumption cost is a noisy copy of the winning price, which flatters cost-plus. See ml/reports/latest_model.md.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

function Models() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["admin", "models"], queryFn: async () => unwrap(await api.GET("/api/admin/model-runs")) });
  const [selected, setSelected] = useState<number | null>(null);
  const activate = useMutation({
    mutationFn: async (id: number) => unwrap(await api.POST("/api/admin/model-runs/{run_id}/activate", { params: { path: { run_id: id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "models"] }),
  });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorState error={q.error} />;
  if (!q.data?.length) return <Empty title="No model runs yet">Run <code>make train</code>.</Empty>;
  const run = q.data.find((r) => r.id === selected) ?? q.data.find((r) => r.is_active) ?? q.data[0];
  return (
    <div className="space-y-4">
      <div className="overflow-hidden rounded-lg border bg-card">
        <Table>
          <THead>
            <tr>
              <TH>Run</TH>
              <TH>Created</TH>
              <TH>Train window</TH>
              <TH>Test window</TH>
              <TH className="text-right">Pinball</TH>
              <TH className="text-right">80% cov.</TH>
              <TH>Status</TH>
              <TH />
            </tr>
          </THead>
          <TBody>
            {q.data.map((r) => {
              const mm = r.metrics as M;
              const model = (mm.model ?? {}) as Record<string, number>;
              return (
                <TR key={r.id} className={r.id === run.id ? "bg-accent/60" : ""} onClick={() => setSelected(r.id)}>
                  <TD className="font-mono">#{r.id}</TD>
                  <TD className="text-[12px]">{formatDateTime(r.created_at)}</TD>
                  <TD className="text-[12px]">{r.train_window}</TD>
                  <TD className="text-[12px]">{r.test_window}</TD>
                  <TD className="num text-right">{mm.pinball_mean?.toFixed(4) ?? "—"}</TD>
                  <TD className="num text-right">{pct(model.coverage_80, 1)}</TD>
                  <TD>{r.is_active ? <Badge variant="success">Active</Badge> : <Badge variant="outline">Inactive</Badge>}</TD>
                  <TD className="text-right">
                    {!r.is_active && (
                      <Button size="xs" variant="ghost" onClick={(e) => (e.stopPropagation(), activate.mutate(r.id))}>
                        Activate
                      </Button>
                    )}
                  </TD>
                </TR>
              );
            })}
          </TBody>
        </Table>
      </div>
      <ModelDetail run={run} />
    </div>
  );
}

function ReviewQueue() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["admin", "review"], queryFn: async () => unwrap(await api.GET("/api/admin/review-queue")) });
  const [prices, setPrices] = useState<Record<number, string>>({});
  const decide = useMutation({
    mutationFn: async ({ id, action }: { id: number; action: "approve" | "reject" }) =>
      unwrap(
        await api.POST("/api/admin/supplier-quotes/{quote_id}/review", {
          params: { path: { quote_id: id } },
          body: { action, unit_price: prices[id] ? Number(prices[id]) : null },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "review"] }),
  });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorState error={q.error} />;
  if (!q.data?.length) return <Empty title="Review queue is empty">Low-confidence and out-of-range quote extractions show up here.</Empty>;
  return (
    <div className="overflow-hidden rounded-lg border bg-card">
      <Table>
        <THead>
          <tr>
            <TH>Organization</TH>
            <TH>Solicitation / NSN</TH>
            <TH>Supplier</TH>
            <TH className="text-right">Parsed price</TH>
            <TH className="text-right">History median</TH>
            <TH>Reason</TH>
            <TH>Source text</TH>
            <TH className="min-w-[220px]">Decision</TH>
          </tr>
        </THead>
        <TBody>
          {q.data.map((i) => (
            <TR key={i.quote.id}>
              <TD className="text-[12px]">{i.organization_name}</TD>
              <TD className="text-[12px]">
                <div className="font-mono">{i.solicitation_number}</div>
                <div className="text-muted-foreground">{formatNsn(i.nsn)} · {i.nomenclature}</div>
              </TD>
              <TD className="font-mono text-[12px]">{i.cage_code}</TD>
              <TD className="num text-right">{money(i.quote.unit_price)}</TD>
              <TD className="num text-right">{money(i.history_median)}</TD>
              <TD className="max-w-[220px] whitespace-normal text-[11px] text-warning">{i.quote.review_reason}</TD>
              <TD className="max-w-[260px]">
                <details className="text-[11px]">
                  <summary className="cursor-pointer">show</summary>
                  <pre className="max-h-32 overflow-auto whitespace-pre-wrap">{i.quote.raw_text}</pre>
                </details>
              </TD>
              <TD>
                <div className="flex items-center gap-1">
                  <Input
                    className="h-7 w-20 text-xs"
                    type="number"
                    step="0.0001"
                    placeholder="price"
                    value={prices[i.quote.id] ?? ""}
                    onChange={(e) => setPrices((p) => ({ ...p, [i.quote.id]: e.target.value }))}
                    aria-label="Corrected unit price"
                  />
                  <Button size="xs" onClick={() => decide.mutate({ id: i.quote.id, action: "approve" })} disabled={decide.isPending}>
                    Approve
                  </Button>
                  <Button size="xs" variant="outline" onClick={() => decide.mutate({ id: i.quote.id, action: "reject" })} disabled={decide.isPending}>
                    Reject
                  </Button>
                </div>
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>
    </div>
  );
}

export default function AdminPage() {
  const { data: me, isLoading } = useMe();
  const overview = useQuery({
    queryKey: ["admin", "overview"],
    queryFn: async () => unwrap(await api.GET("/api/admin/overview")),
    enabled: Boolean(me?.user.is_superadmin),
  });
  if (isLoading) return <Loading />;
  if (!me?.user.is_superadmin) return <ErrorState error={new Error("Superadmin access required.")} />;
  const o = overview.data;
  return (
    <div>
      <PageHeader title="Admin" description="Platform-wide ingestion, models and quote review." actions={<Triggers />} />
      {o && (
        <div className="mb-4 grid grid-cols-2 gap-2 md:grid-cols-6">
          <Stat label="Organizations" value={num(o.organizations)} hint={`${num(o.users)} users`} />
          <Stat label="Solicitations" value={num(o.solicitations)} hint={`${num(o.open_lines)} open lines`} />
          <Stat label="Award rows" value={num(o.awards)} hint={`${num(o.nsns_with_history)} NSNs with history`} />
          <Stat label="Pending review" value={num(o.pending_review)} tone={o.pending_review ? "warn" : undefined} />
          <Stat label="Connector" value={o.connector_mode} hint={o.demo_mode ? "demo mode" : "production"} />
          <Stat label="Providers" value={o.llm_provider} hint={`email: ${o.email_provider} · voice: ${o.voice_enabled ? "on" : "off"}`} />
        </div>
      )}
      <Tabs defaultValue="models">
        <TabsList>
          <TabsTrigger value="models">Models</TabsTrigger>
          <TabsTrigger value="runs">Ingestion runs</TabsTrigger>
          <TabsTrigger value="review">Review queue</TabsTrigger>
        </TabsList>
        <TabsContent value="models" className="mt-3">
          <Models />
        </TabsContent>
        <TabsContent value="runs" className="mt-3">
          <IngestionRuns />
        </TabsContent>
        <TabsContent value="review" className="mt-3">
          <ReviewQueue />
        </TabsContent>
      </Tabs>
    </div>
  );
}
