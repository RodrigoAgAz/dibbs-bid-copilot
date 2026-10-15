"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Check, Copy, ExternalLink, FileText, RefreshCw } from "lucide-react";
import { Contributions, HistoryChart, PWinChart, WaterfallChart } from "@/components/rfq/charts";
import { SupplierPanel } from "@/components/rfq/supplier-panel";
import { ErrorState, FlagBadges, Loading, WriteGuard } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge, Label, Tip } from "@/components/ui/misc";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, errorMessage, unwrap, type Schemas } from "@/lib/api/client";
import { useMe } from "@/lib/hooks";
import { competitors, priceStory, type Award } from "@/lib/insights";
import { REFERENCE_LABELS, countdown, formatDate, formatDateTime, formatNsn, formatSolicitation, isDemoSolicitation, modelLabel, money, num, pct, plural } from "@/lib/format";
import { cn } from "@/lib/utils";

type Sol = Schemas["SolicitationDetail"];
type Line = Schemas["LineDetail"];

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="text-[13px] font-medium">{children ?? "—"}</div>
    </div>
  );
}

function Header({ sol }: { sol: Sol }) {
  const c = countdown(sol.days_left);
  const demo = isDemoSolicitation(sol.solicitation_number);
  return (
    <Card className="mb-4">
      <CardContent className="grid gap-4 p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="font-mono text-lg font-semibold">{formatSolicitation(sol.solicitation_number)}</h1>
              <Badge variant="secondary">{sol.type_label}</Badge>
              {sol.status && <Badge variant={sol.status === "Awarded" ? "success" : "outline"}>{sol.status}</Badge>}
              {demo && <Badge variant="warning">Demo solicitation</Badge>}
            </div>
            <div className="mt-1 text-xs text-muted-foreground">
              Return by <span className={cn("font-medium", c.tone === "danger" && "text-destructive", c.tone === "warn" && "text-warning")}>{formatDate(sol.return_by_date)} ({c.label})</span> · issued {formatDate(sol.issue_date)}
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {demo ? (
              <Tip content="Synthetic demo solicitation: it doesn't exist on DIBBS.">
                <span>
                  <Button variant="outline" size="sm" disabled>
                    <ExternalLink /> View on DIBBS
                  </Button>
                </span>
              </Tip>
            ) : (
              <>
                <Button variant="outline" size="sm" asChild>
                  <a href={sol.dibbs_url} target="_blank" rel="noreferrer">
                    <ExternalLink /> View on DIBBS
                  </a>
                </Button>
                {sol.pdf_url && (
                  <Button variant="outline" size="sm" asChild>
                    <a href={sol.pdf_url} target="_blank" rel="noreferrer">
                      <FileText /> RFQ PDF
                    </a>
                  </Button>
                )}
              </>
            )}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4 lg:grid-cols-8">
          <Fact label="Buyer">{sol.buyer_name ?? sol.buyer_code}</Fact>
          <Fact label="Buyer email">{sol.buyer_email ? <a className="text-primary hover:underline" href={`mailto:${sol.buyer_email}`}>{sol.buyer_email}</a> : null}</Fact>
          <Fact label="Set-aside">{sol.set_aside}</Fact>
          <Fact label="NAICS">{sol.naics}</Fact>
          <Fact label="Inspection">{sol.inspection_point}</Fact>
          <Fact label="FOB">{sol.fob}</Fact>
          <Fact label="First article">{sol.first_article ? "Required" : "No"}</Fact>
          <Fact label="Flags">
            <FlagBadges flags={{ fast_award: sol.fast_award, aidc: sol.aidc }} />
          </Fact>
        </div>
        {sol.packaging && <div className="text-xs text-muted-foreground">Packaging: {sol.packaging}</div>}
      </CardContent>
    </Card>
  );
}

function CopyBid({ value }: { value: number | null | undefined }) {
  const [done, setDone] = useState(false);
  if (value == null) return null;
  return (
    <Button
      size="sm"
      variant="outline"
      onClick={async () => {
        await navigator.clipboard?.writeText(value.toFixed(value < 1 ? 4 : 2));
        setDone(true);
        setTimeout(() => setDone(false), 1500);
      }}
      data-testid="copy-bid"
    >
      {done ? <Check /> : <Copy />} {done ? "Copied" : "Copy bid"}
    </Button>
  );
}

const EVIDENCE_WORD: Record<string, string> = { part_number: "part number", unit: "pricing unit", lead_time: "lead time", validity: "validity", freight: "freight" };

/** Under the landed cost: which facts of the quote it rests on were assumed or never stated. */
function QuoteEvidenceNote({ evidence }: { evidence: Record<string, string> }) {
  const weak = Object.entries(evidence)
    .filter(([, v]) => v !== "confirmed")
    .map(([k, v]) => `${EVIDENCE_WORD[k] ?? k} ${v === "inferred" ? "assumed" : "not stated"}`);
  if (!weak.length) return <div className="text-[11px] text-success">supplier confirmed every term</div>;
  return (
    <div className="text-[11px] text-warning" data-testid="quote-evidence-note">
      {weak.join(" · ")}
    </div>
  );
}

function ActualBid({ line, sn }: { line: Line; sn: string }) {
  const qc = useQueryClient();
  const [v, setV] = useState(line.recommendation?.actual_bid != null ? String(line.recommendation.actual_bid) : "");
  useEffect(() => setV(line.recommendation?.actual_bid != null ? String(line.recommendation.actual_bid) : ""), [line.recommendation?.actual_bid]);
  const save = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST("/api/rfq/lines/{line_id}/actual-bid", { params: { path: { line_id: line.id } }, body: { actual_bid: v ? Number(v) : null } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["rfq", sn] });
      qc.invalidateQueries({ queryKey: ["history"] });
    },
  });
  return (
    <WriteGuard>
    <form
      className="flex items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div className="grid gap-1">
        <Label htmlFor={`ab-${line.id}`}>What I actually bid (per unit)</Label>
        <Input id={`ab-${line.id}`} className="h-8 w-32 text-xs" type="number" step="0.0001" min="0" value={v} onChange={(e) => setV(e.target.value)} />
      </div>
      <Button size="sm" variant="outline" type="submit" disabled={save.isPending}>
        {save.isSuccess ? <Check /> : null} Save
      </Button>
      {save.error ? <span className="text-xs text-destructive">{errorMessage(save.error)}</span> : null}
    </form>
    </WriteGuard>
  );
}

function ActualCost({ line, sn }: { line: Line; sn: string }) {
  const qc = useQueryClient();
  const current = line.recommendation?.actual_cost_per_unit;
  const [v, setV] = useState(current != null ? String(current) : "");
  useEffect(() => setV(current != null ? String(current) : ""), [current]);
  const save = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST("/api/rfq/lines/{line_id}/actual-cost", { params: { path: { line_id: line.id } }, body: { actual_cost_per_unit: v ? Number(v) : null } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["rfq", sn] });
      qc.invalidateQueries({ queryKey: ["history"] });
    },
  });
  return (
    <WriteGuard>
      <form
        className="flex items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <div className="grid gap-1">
          <Label htmlFor={`ac-${line.id}`}>What I actually paid (per unit, landed)</Label>
          <Input id={`ac-${line.id}`} className="h-8 w-32 text-xs" type="number" step="0.0001" min="0" value={v} onChange={(e) => setV(e.target.value)} />
        </div>
        <Button size="sm" variant="outline" type="submit" disabled={save.isPending}>
          {save.isSuccess ? <Check /> : null} Save
        </Button>
        {save.error ? <span className="text-xs text-destructive">{errorMessage(save.error)}</span> : null}
      </form>
    </WriteGuard>
  );
}

type CostEstimate = { median: number; q10: number; q90: number; tier: string; n: number; basis: string; ratio?: number | null };
type CompetitorProfile = {
  cage: string;
  name: string | null;
  wins: number;
  nsns: number;
  last_win: string | null;
  is_manufacturer: boolean;
  share: number;
  median_ratio: number | null;
  ratio_sigma: number | null;
  n_ratio: number;
};
type CompetitorsInfo = {
  fsc: string;
  n_awards: number;
  incumbents: CompetitorProfile[];
  fsc_players: CompetitorProfile[];
  reseller_share_nsn: number | null;
  reseller_share_fsc: number;
  p_winnable: number;
  reason: string;
};

/** "−5%" when a competitor typically wins 5% under the inflation-adjusted previous award, "+3%" above it. */
function undercut(ratio: number): string {
  const d = Math.round((ratio - 1) * 100);
  return d === 0 ? "±0%" : d < 0 ? `−${-d}%` : `+${d}%`;
}
type TriageInfo = {
  score: number;
  expected_profit_if_sourced: number;
  p_sourceable: number;
  supplier_price_ceiling: number | null;
  p_quote_in_time: number;
  candidates: number;
  candidates_with_email: number;
  days_left: number | null;
  p_winnable: number;
  winnable_reason: string;
  pursuit_cost: number;
};

/** Where the assumed supplier price comes from: the tenant's own quotes (by tier) or the starting assumption. */
function CostEstimateNote({ est }: { est: CostEstimate }) {
  return (
    <p className="mt-2 text-[11px] text-muted-foreground" data-testid="cost-estimate">
      Assumed supplier price <b className="num">{money(est.median)}</b> (likely {money(est.q10)} to {money(est.q90)}), from {est.basis}.
    </p>
  );
}

/** Is this line worth the sourcing effort? Every factor of the score, so the broker can see why it ranks where it does. */
function TriageBox({ t }: { t: TriageInfo }) {
  const worth = t.score > 0;
  return (
    <div className={cn("mt-2 rounded-md border p-2", worth ? "border-success/40 bg-success/5" : "border-border bg-background")} data-testid="triage">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-semibold">{worth ? "Worth pursuing" : "Probably not worth the effort"}</span>
        <span className="num">
          Triage score <b>{money(t.score, { digits: 0 })}</b>
        </span>
      </div>
      <ul className="mt-1 grid gap-x-4 gap-y-0.5 text-[11px] sm:grid-cols-2">
        <li>
          Profit if sourced at the assumed cost: <b className="num">{money(t.expected_profit_if_sourced, { digits: 0 })}</b>
        </li>
        <li>
          Chance a supplier price lands under {t.supplier_price_ceiling != null ? money(t.supplier_price_ceiling) : "the target"}:{" "}
          <b className="num">{pct(t.p_sourceable)}</b>
        </li>
        <li>
          Chance one of {plural(t.candidates, "candidate")} ({t.candidates_with_email} with an email) answers in time: <b className="num">{pct(t.p_quote_in_time)}</b>
        </li>
        <li>
          Winnable by a broker: <b className="num">{pct(t.p_winnable)}</b> <span className="text-muted-foreground">({t.winnable_reason})</span>
        </li>
        <li>
          Effort charged: <b className="num">{money(t.pursuit_cost, { digits: 0 })}</b>
        </li>
      </ul>
    </div>
  );
}

type EvaluationInfo = {
  mode: string;
  price_only: boolean;
  manual_reasons: string[];
  cap_bid: number | null;
  cap_ratio: number | null;
  cap_reference: number | null;
  cap_n: number;
  cap_source: string | null;
  cap_binding: boolean;
  required_days: number | null;
  quote_lead_days: number | null;
  handling_days: number;
  delivery_feasible: boolean | null;
  return_by: string | null;
  effective_deadline: string | null;
  effective_days_left: number | null;
  exceptions: string[];
  factors: { label: string; amount: number }[];
  notes: string[];
};

const MODE_LABEL: Record<string, string> = {
  automated: "Automated evaluation: price alone among qualified quotes",
  fast_automated: "Fast auto evaluation: price alone, may award before the return date",
  aidc: "Automated IDC: price alone, a one-year contract",
  manual: "Manual evaluation by a buyer",
};

/** How DLA evaluates this buy (Master Solicitation, Part I) and what that did to the bid. */
function EvaluationBox({ ev }: { ev: EvaluationInfo }) {
  return (
    <details className="rounded-md border bg-muted/30 p-2 text-xs" data-testid="evaluation" open={ev.cap_binding || ev.exceptions.length > 0}>
      <summary className="cursor-pointer font-semibold">
        How DLA evaluates this: {MODE_LABEL[ev.mode] ?? ev.mode}
        {ev.cap_binding ? <span className="ml-2 rounded border border-warning/50 px-1.5 py-0.5 text-[11px] text-warning">bid capped</span> : null}
        {ev.exceptions.length > 0 ? <span className="ml-2 rounded border border-destructive/50 px-1.5 py-0.5 text-[11px] text-destructive">{plural(ev.exceptions.length, "warning")}</span> : null}
      </summary>
      <ul className="mt-1.5 space-y-1">
        {ev.manual_reasons.map((m) => (
          <li key={m}>{m.charAt(0).toUpperCase() + m.slice(1)}.</li>
        ))}
        {ev.cap_bid != null ? (
          <li>
            {ev.cap_binding ? "The bid is capped at " : "Price band: bids up to "}
            <b className="num">{money(ev.cap_bid)}</b>
            {ev.cap_source ? <span className="text-muted-foreground"> ({ev.cap_source})</span> : null}.
          </li>
        ) : ev.price_only ? (
          <li className="text-muted-foreground">No automated price band yet for this supply class (too few awards on file); the model&apos;s range alone bounds the bid.</li>
        ) : null}
        {ev.required_days != null ? (
          <li>
            Required delivery {ev.required_days} days after order
            {ev.quote_lead_days != null ? (
              <>
                ; the quote&apos;s lead time is {ev.quote_lead_days} d + {ev.handling_days} d handling:{" "}
                <b className={ev.delivery_feasible ? "text-success" : "text-warning"}>{ev.delivery_feasible ? "fits" : "does not fit"}</b>
              </>
            ) : (
              <span className="text-muted-foreground">; a supplier quote must fit it with {ev.handling_days} d handling</span>
            )}
            .
          </li>
        ) : null}
        {ev.effective_deadline && ev.return_by && ev.effective_deadline !== ev.return_by ? (
          <li className="text-warning">Sourcing deadline {formatDate(ev.effective_deadline)}, before the return-by date {formatDate(ev.return_by)}.</li>
        ) : null}
        {ev.exceptions.map((x) => (
          <li key={x} className="text-destructive">
            {x.charAt(0).toUpperCase() + x.slice(1)}.
          </li>
        ))}
        {ev.notes.map((n) => (
          <li key={n} className="text-muted-foreground">
            {n.charAt(0).toUpperCase() + n.slice(1)}.
          </li>
        ))}
        {ev.factors.length > 0 ? (
          <li className="text-muted-foreground">
            Evaluation factors DLA adds to a quote&apos;s price: {ev.factors.map((f) => `${f.label} (+${money(f.amount, { digits: 0 })})`).join("; ")}.
          </li>
        ) : null}
      </ul>
    </details>
  );
}

type SensitivityInfo = {
  fragile: boolean;
  scenarios: { name: string; recommended_bid: number | null; p_win: number | null; expected_profit: number | null; no_bid: boolean; bid_change_pct: number | null; flips: boolean }[];
};

/** Which plausible change would flip the decision: the same engine re-run on three scenarios. */
function SensitivityBox({ s, base }: { s: SensitivityInfo; base: number | null | undefined }) {
  return (
    <details className={cn("rounded-md border p-2 text-xs", s.fragile ? "border-warning/50 bg-warning/5" : "bg-muted/30")} data-testid="sensitivity" open={s.fragile}>
      <summary className="cursor-pointer font-semibold">
        {s.fragile ? "Fragile: a plausible change flips this decision" : "Robust to plausible changes"}
      </summary>
      <table className="mt-1.5 w-full text-[11px]">
        <thead className="text-muted-foreground">
          <tr>
            <th className="text-left font-medium">If…</th>
            <th className="text-right font-medium">Bid</th>
            <th className="text-right font-medium">P(win)</th>
            <th className="text-right font-medium">Expected profit</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>as recommended</td>
            <td className="num text-right">{money(base)}</td>
            <td />
            <td />
          </tr>
          {s.scenarios.map((sc) => (
            <tr key={sc.name} className={cn(sc.flips && "text-warning")}>
              <td>{sc.name}</td>
              <td className="num text-right">
                {sc.no_bid ? "no bid" : money(sc.recommended_bid)}
                {sc.bid_change_pct != null && !sc.no_bid ? <span className="ml-1 text-muted-foreground">({sc.bid_change_pct >= 0 ? "+" : "−"}{Math.abs(Math.round(sc.bid_change_pct * 100))}%)</span> : null}
              </td>
              <td className="num text-right">{sc.no_bid ? "—" : pct(sc.p_win)}</td>
              <td className="num text-right">{sc.no_bid ? "—" : money(sc.expected_profit, { digits: 0 })}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

const SNAPSHOT_KIND: Record<string, string> = { recommendation: "Recommended", actual_bid: "You bid", actual_cost: "You paid" };

/** Append-only record of what the tool showed and what the broker recorded, oldest first. */
function DecisionHistory({ lineId }: { lineId: number }) {
  const [open, setOpen] = useState(false);
  const q = useQuery({
    queryKey: ["snapshots", lineId],
    queryFn: async () => unwrap(await api.GET("/api/rfq/lines/{line_id}/snapshots", { params: { path: { line_id: lineId } } })),
    enabled: open,
  });
  return (
    <details className="text-xs" onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)} data-testid="decision-history">
      <summary className="cursor-pointer text-muted-foreground">Decision history</summary>
      {q.isLoading ? (
        <p className="mt-1 text-muted-foreground">Loading…</p>
      ) : !q.data?.length ? (
        <p className="mt-1 text-muted-foreground">No snapshots yet.</p>
      ) : (
        <ul className="mt-1 space-y-0.5">
          {q.data.map((s) => {
            const p = s.payload as Record<string, unknown>;
            return (
              <li key={s.id} className="flex flex-wrap gap-x-3 gap-y-0.5">
                <span className="w-32 shrink-0 text-muted-foreground">{formatDateTime(s.created_at)}</span>
                <span className="font-medium">{SNAPSHOT_KIND[s.kind] ?? s.kind}</span>
                {s.kind === "recommendation" ? (
                  <span>
                    {p.no_bid ? "no bid" : `bid ${money(p.recommended_bid as number | null)} · P(win) ${pct(p.p_win as number | null)}`}
                    {" · landed "}
                    {money(p.landed_cost_per_unit as number | null)}
                    {p.provisional ? " (assumed)" : " (quoted)"}
                  </span>
                ) : s.kind === "actual_bid" ? (
                  <span>{money(p.actual_bid as number | null)}</span>
                ) : (
                  <span>{money(p.actual_cost_per_unit as number | null)}</span>
                )}
                {s.engine_version && <span className="text-muted-foreground">engine v{s.engine_version}</span>}
              </li>
            );
          })}
        </ul>
      )}
    </details>
  );
}

/** Parsed supplier quotes that wait for the broker's approval before they count as a cost. */
function pendingReview(line: Line): number {
  return line.candidates
    .flatMap((c) => c.quote_requests)
    .flatMap((q) => q.quotes ?? [])
    .filter((q) => q.needs_review && !q.reviewed_at && !q.declined && q.unit_price != null).length;
}

function RecommendationCard({ line, sn }: { line: Line; sn: string }) {
  const qc = useQueryClient();
  const r = line.recommendation;
  const refresh = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/rfq/lines/{line_id}/refresh", { params: { path: { line_id: line.id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["rfq", sn] }),
  });
  if (!r) return null;
  const ex = (r.explanation ?? {}) as Record<string, unknown>;
  const review = pendingReview(line);
  const range = Array.isArray(ex.price_range) ? (ex.price_range as number[]) : null;
  return (
    <Card data-testid="recommendation">
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <div>
          <CardTitle>Recommendation</CardTitle>
          <CardDescription>
            Maximizes P(win) × (bid − landed cost) × quantity
            {typeof ex.confidence === "string" && ex.confidence !== "none" ? (
              <span className="ml-2 rounded border px-1.5 py-0.5 text-[11px]" data-testid="confidence">
                {ex.confidence} confidence{typeof ex.n_prior_awards === "number" ? ` · ${plural(ex.n_prior_awards, "past award")}` : ""}
              </span>
            ) : null}
          </CardDescription>
        </div>
        <WriteGuard className="w-auto">
          <Button size="xs" variant="ghost" onClick={() => refresh.mutate()} disabled={refresh.isPending} aria-label="Recompute">
            <RefreshCw className={cn(refresh.isPending && "animate-spin")} />
          </Button>
        </WriteGuard>
      </CardHeader>
      <CardContent className="space-y-3">
        {r.no_bid ? (
          <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3">
            <div className="text-sm font-semibold text-destructive" data-testid="no-bid">No bid</div>
            <ul className="mt-1 list-disc pl-4 text-xs">
              {(r.no_bid_reason ?? "").split("; ").map((x) => (
                <li key={x}>{x.charAt(0).toUpperCase() + x.slice(1)}</li>
              ))}
            </ul>
          </div>
        ) : null}
        <div className="flex flex-wrap items-end gap-6">
          <div>
            <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Recommended bid</div>
            <div className={cn("num text-3xl font-semibold", r.no_bid && "text-muted-foreground line-through decoration-1")} data-testid="recommended-bid">
              {money(r.recommended_bid)}
            </div>
            <div className="text-xs text-muted-foreground">per {line.unit_of_issue}</div>
          </div>
          <div>
            <div className="text-[11px] uppercase tracking-wide text-muted-foreground">P(win)</div>
            <div className="num text-xl font-semibold">{pct(r.p_win_at_recommended)}</div>
          </div>
          <div>
            <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
              {ex.expected_profit_basis === "assumed_cost" ? "Profit if sourced" : "Expected profit"}
            </div>
            <div
              className={cn("num text-xl font-semibold", r.no_bid ? "text-muted-foreground" : ex.expected_profit_basis === "assumed_cost" ? "text-foreground" : "text-success")}
              data-testid="expected-profit"
            >
              {money(r.expected_profit, { digits: 0 })}
            </div>
            {ex.expected_profit_basis === "assumed_cost" && typeof ex.max_landed_cost === "number" && (
              <div className="text-[11px] text-muted-foreground">if landed cost ≤ {money(ex.max_landed_cost as number)}</div>
            )}
          </div>
          <div>
            <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{r.provisional ? "Assumed cost" : "Landed cost"}</div>
            <div className="num text-xl font-semibold">{money(r.landed_cost_per_unit)}</div>
            {!r.provisional && ex.quote_evidence && typeof ex.quote_evidence === "object" ? (
              <QuoteEvidenceNote evidence={ex.quote_evidence as Record<string, string>} />
            ) : null}
          </div>
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          {r.conservative_bid != null && (
            <Tip content="Safer: the highest bid that still wins at least 70% of the time">
              <span className="rounded-md border px-2 py-1">
                Conservative <b className="num">{money(r.conservative_bid)}</b>
                {ex.conservative_p_win != null && <span className="text-muted-foreground"> · {pct(ex.conservative_p_win as number)}</span>}
              </span>
            </Tip>
          )}
          {r.aggressive_bid != null && (
            <Tip content="Riskier: more margin, lower chance of winning (about 30%, or your minimum)">
              <span className="rounded-md border px-2 py-1">
                Aggressive <b className="num">{money(r.aggressive_bid)}</b>
                {ex.aggressive_p_win != null && <span className="text-muted-foreground"> · {pct(ex.aggressive_p_win as number)}</span>}
              </span>
            </Tip>
          )}
          <FlagBadges
            empty={null}
            flags={{ provisional: r.provisional, low_data: Boolean(ex.low_data), cold_start: Boolean(ex.cold_start), approved_source_only: line.approved_source_only, needs_quote: Boolean(ex.needs_quote) }}
          />
        </div>
        {review > 0 && (
          <p className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs" data-testid="review-pending">
            {review === 1 ? "A parsed supplier quote is" : `${review} parsed supplier quotes are`} waiting for your review in Suppliers &amp; quotes
            below. Approve {review === 1 ? "it" : "them"} to price this line against the real cost.
          </p>
        )}
        {ex.no_reference ? (
          <div className="rounded-md border border-warning/40 bg-warning/10 p-3 text-xs" data-testid="no-reference">
            <div className="text-sm font-semibold">No price reference</div>
            DLA has no usable award history for this NSN, its part family or its supply class, so there is no winning price to
            predict.{" "}
            {typeof ex.cost_plus_bid === "number" ? (
              <>
                At your minimum margin over the quoted cost, bid at least <b className="num">{money(ex.cost_plus_bid as number)}</b>.
              </>
            ) : (
              "Get a supplier quote: the line is then priced at your minimum margin over cost."
            )}
          </div>
        ) : ex.needs_quote ? (
          <div className="rounded-md border border-warning/40 bg-warning/10 p-3 text-xs" data-testid="quote-first">
            <div className="text-sm font-semibold">Quote first</div>
            {range
              ? `This NSN has no usable award history; similar items put the winning price roughly between ${money(range[0])} and ${money(range[1])} (80% range).`
              : "This NSN has no usable award history, and similar items don't narrow the winning price down."}{" "}
            A bid computed from that would be a guess: request supplier quotes below and the recommendation appears as soon as one is in.
          </div>
        ) : r.provisional && !r.no_bid && typeof ex.max_landed_cost === "number" ? (
          <div className="rounded-md border bg-muted/50 p-3 text-xs" data-testid="target-cost">
            <div className="text-sm font-semibold">
              Target: get a landed supplier cost under <span className="num">{money(ex.max_landed_cost as number)}</span>
            </div>
            No supplier quote yet, so this bid is set where it is likely to win ({pct(r.p_win_at_recommended)} chance), not optimized
            against a guessed cost. A supplier quote at or below the target keeps your minimum margin; once quotes are in, the bid is
            optimized against your real cost.
            {ex.cost_estimate && typeof ex.cost_estimate === "object" ? <CostEstimateNote est={ex.cost_estimate as CostEstimate} /> : null}
            {ex.triage && typeof ex.triage === "object" ? <TriageBox t={ex.triage as TriageInfo} /> : null}
          </div>
        ) : r.provisional && !r.no_bid && (
          <p className="rounded-md bg-muted px-2 py-1.5 text-xs text-muted-foreground">
            Provisional: no supplier quote yet. Landed cost is estimated from the predicted winning price. Send RFQ emails below to firm it up.
          </p>
        )}
        <div className="flex flex-wrap items-end justify-between gap-3 border-t pt-3">
          {r.no_bid ? <span /> : <CopyBid value={r.recommended_bid} />}
          <div className="flex flex-wrap items-end gap-3">
            <ActualBid line={line} sn={sn} />
            {(line.outcome as { won?: boolean } | null)?.won && <ActualCost line={line} sn={sn} />}
          </div>
        </div>
        {ex.evaluation && typeof ex.evaluation === "object" ? <EvaluationBox ev={ex.evaluation as EvaluationInfo} /> : null}
        {ex.sensitivity && typeof ex.sensitivity === "object" && !r.no_bid ? (
          <SensitivityBox s={ex.sensitivity as SensitivityInfo} base={r.recommended_bid} />
        ) : null}
        <DecisionHistory lineId={line.id} />
        <p className="text-[11px] text-muted-foreground">
          The Invisible Hand never submits bids. Review the recommendation and submit your quote on DIBBS yourself.
        </p>
      </CardContent>
    </Card>
  );
}

function LineSection({ line, sol, voiceEnabled, demo }: { line: Line; sol: Sol; voiceEnabled: boolean; demo: boolean }) {
  const r = line.recommendation;
  const ex = (r?.explanation ?? {}) as {
    curve?: { bid: number; p_win: number; expected_profit: number }[];
    needs_quote?: boolean;
    no_reference?: boolean;
    competitors?: CompetitorsInfo | null;
    contestability?: { p_winnable: number; reason: string } | null;
    competitor_p_win?: number | null;
  };
  const profiles = new Map<string, CompetitorProfile>();
  for (const c of [...(ex.competitors?.incumbents ?? []), ...(ex.competitors?.fsc_players ?? [])]) profiles.set(c.cage, c);
  const p = line.prediction;
  // Without a quote or any price history the curve and the cost waterfall would be drawn from a guess.
  const guessOnly = Boolean(ex.needs_quote || ex.no_reference || !p);
  const history = line.history as Award[];
  const unverified = history.filter((a) => a.verified === false).length;
  const basis = (r?.landed_breakdown as { basis?: string } | null)?.basis ?? "";
  return (
    <section className="scroll-mt-16 space-y-4" data-testid="line-section" id={`line-${line.line_no}`}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="text-base font-semibold">
          Line {line.line_no}: {line.nomenclature}
        </h2>
        <span className="font-mono text-xs text-muted-foreground">NSN {formatNsn(line.nsn)}</span>
        <span className="text-xs">
          Qty <b className="num">{num(line.quantity)}</b> {line.unit_of_issue}
        </span>
        {line.delivery_days && <span className="text-xs text-muted-foreground">deliver in {line.delivery_days} days</span>}
        {line.amsc && <span className="text-xs text-muted-foreground">AMSC {line.amsc}</span>}
        {line.dod_standard_price != null && <span className="text-xs text-muted-foreground">DoD std price {money(line.dod_standard_price)}</span>}
        {line.outcome && (
          <Badge variant={(line.outcome as { won?: boolean }).won ? "success" : "outline"}>
            {(line.outcome as { won?: boolean }).won ? "You won" : "Awarded to " + String((line.outcome as { awardee_cage?: string }).awardee_cage)} at{" "}
            {money((line.outcome as { winning_price?: number }).winning_price)}
          </Badge>
        )}
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <RecommendationCard line={line} sn={sol.solicitation_number} />
        <Card>
          <CardHeader>
            <CardTitle>P(win) vs bid</CardTitle>
            <CardDescription>
              Chance the winning price is above your bid, and the expected profit at each bid
              {p && !guessOnly ? ` (predicted median ${money(p.median)}, ${modelLabel(p.model)})` : ""}
            </CardDescription>
          </CardHeader>
          <CardContent className="pt-2">
            {guessOnly ? (
              <div className="flex h-56 items-center justify-center px-6 text-center text-xs text-muted-foreground" data-testid="pwin-hidden">
                Appears once a supplier quote is in: without award history or a real cost, this curve would be drawn from a guess.
              </div>
            ) : (
              <PWinChart
                curve={ex.curve ?? []}
                landed={r?.landed_cost_per_unit ?? null}
                recommended={r?.recommended_bid ?? null}
                conservative={r?.conservative_bid ?? null}
                aggressive={r?.aggressive_bid ?? null}
              />
            )}
          </CardContent>
        </Card>
      </div>

      <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
        <Card>
          <CardHeader>
            <CardTitle>Price story</CardTitle>
            <CardDescription>What the award history says, in plain English</CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="list-disc space-y-1.5 pl-4 text-sm" data-testid="price-story">
              {priceStory(history, line.quantity).map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Who wins this NSN</CardTitle>
            <CardDescription>
              Past awardees, most wins first
              {ex.contestability ? (
                <span className="ml-2 rounded border px-1.5 py-0.5 text-[11px]" data-testid="contestability">
                  winnable by a broker: {pct(ex.contestability.p_winnable)}
                </span>
              ) : null}
            </CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <THead>
                <tr>
                  <TH>Awardee</TH>
                  <TH className="text-right">Wins</TH>
                  <TH className="text-right">Last price</TH>
                  <TH className="text-right">vs last price</TH>
                  <TH>Last award</TH>
                </tr>
              </THead>
              <TBody>
                {competitors(history).map((c) => {
                  const p = profiles.get(c.cage);
                  return (
                    <TR key={c.cage}>
                      <TD className="max-w-[200px] truncate" title={c.name ?? ""}>
                        {c.name ?? <span className="text-muted-foreground">Unknown</span>}
                        <div className="font-mono text-[11px] text-muted-foreground">
                          {c.cage === "unknown" ? "—" : c.cage}
                          {p ? <span className="ml-1 font-sans">{p.is_manufacturer ? "· manufacturer" : "· reseller"}</span> : null}
                        </div>
                      </TD>
                      <TD className="num text-right">{c.wins}</TD>
                      <TD className="num text-right">{c.lastPrice == null ? <span className="text-muted-foreground">unverified</span> : money(c.lastPrice)}</TD>
                      <TD className="num text-right">
                        {p?.median_ratio != null ? (
                          <Tip content={`Typically wins at ${pct(p.median_ratio)} of the inflation-adjusted previous award (${plural(p.n_ratio, "award")} in this supply class)`}>
                            <span>{undercut(p.median_ratio)}</span>
                          </Tip>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </TD>
                      <TD>{formatDate(c.lastDate)}</TD>
                    </TR>
                  );
                })}
                {!line.history.length && (
                  <TR>
                    <TD colSpan={5} className="text-center text-xs text-muted-foreground">
                      No past awards for this NSN.
                    </TD>
                  </TR>
                )}
              </TBody>
            </Table>
            {ex.competitors ? (
              <div className="space-y-1 border-t px-3 py-2 text-xs" data-testid="competitors">
                {ex.contestability ? <p>{ex.contestability.reason.charAt(0).toUpperCase() + ex.contestability.reason.slice(1)}.</p> : null}
                {ex.competitors.fsc_players.length > 0 && (
                  <p className="text-muted-foreground">
                    Also active in FSC {ex.competitors.fsc}:{" "}
                    {ex.competitors.fsc_players
                      .slice(0, 5)
                      .map((p) => `${p.name ?? p.cage} (${plural(p.wins, "win")}${p.median_ratio != null ? `, ${undercut(p.median_ratio)}` : ""})`)
                      .join(", ")}
                    .
                  </p>
                )}
                {typeof ex.competitor_p_win === "number" && r?.p_win_at_recommended != null ? (
                  <p className="text-muted-foreground">
                    Competitor view of the recommended bid: {pct(ex.competitor_p_win)} chance nobody known comes in under it (model: {pct(r.p_win_at_recommended)}).
                    Winning prices only, so this view runs low; the model&apos;s figure is the calibrated one.
                  </p>
                ) : null}
              </div>
            ) : null}
          </CardContent>
        </Card>
      </div>

      {(p?.similar_items ?? []).length > 0 && (
        <Card data-testid="similar-items">
          <CardHeader>
            <CardTitle>Similar parts DLA bought</CardTitle>
            <CardDescription>
              Same manufacturer part-number family. Prices adjusted for inflation and to this quantity
              {p?.cold_start ? "; with no history for this NSN, they are the best evidence of its price" : ""}.
            </CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <THead>
                <tr>
                  <TH>Part</TH>
                  <TH>Why similar</TH>
                  <TH>Last award</TH>
                  <TH className="text-right">Qty</TH>
                  <TH className="text-right">Unit price</TH>
                  <TH className="text-right">At this qty</TH>
                </tr>
              </THead>
              <TBody>
                {(p?.similar_items ?? []).map((raw) => {
                  const it = raw as {
                    nsn: string;
                    nomenclature?: string | null;
                    reason: string;
                    award_date: string;
                    quantity: number;
                    unit_price: number;
                    adjusted_price: number;
                  };
                  return (
                    <TR key={it.nsn}>
                      <TD className="max-w-[240px] truncate" title={it.nomenclature ?? ""}>
                        {it.nomenclature ?? "—"}
                        <div className="font-mono text-[11px] text-muted-foreground">{formatNsn(it.nsn)}</div>
                      </TD>
                      <TD className="text-[12px]">{it.reason}</TD>
                      <TD>{formatDate(it.award_date)}</TD>
                      <TD className="num text-right">{num(it.quantity)}</TD>
                      <TD className="num text-right">{money(it.unit_price)}</TD>
                      <TD className="num text-right font-medium">{money(it.adjusted_price)}</TD>
                    </TR>
                  );
                })}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Procurement history</CardTitle>
            <CardDescription>
              {history.length
                ? `${plural(history.length, "past award")} for this NSN${unverified ? ` (${num(unverified)} unverified)` : ""}${p && !guessOnly ? " · shaded band = predicted 10–90% range" : ""}`
                : "No past awards on file for this NSN"}
            </CardDescription>
          </CardHeader>
          <CardContent className="pt-2">
            <HistoryChart awards={history} levels={guessOnly ? undefined : p?.levels} prices={guessOnly ? undefined : p?.prices} quantity={line.quantity} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Landed cost per unit</CardTitle>
            <CardDescription>{basis ? basis.charAt(0).toUpperCase() + basis.slice(1) : ""}</CardDescription>
          </CardHeader>
          <CardContent className="pt-2">
            {guessOnly && r?.provisional ? (
              <div className="flex h-40 items-center justify-center px-6 text-center text-xs text-muted-foreground">
                Appears once a supplier quote is in.
              </div>
            ) : (
              <WaterfallChart breakdown={r?.landed_breakdown as Record<string, number> | null} />
            )}
            {(r?.landed_breakdown as { moq_note?: string } | null)?.moq_note && (
              <p className="text-xs text-warning">{(r?.landed_breakdown as { moq_note?: string }).moq_note}</p>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Why this price</CardTitle>
            <CardDescription>
              What moves the predicted median away from the reference price
              {p?.reference_price ? ` (${REFERENCE_LABELS[p.reference_type ?? ""] ?? p.reference_type} ${money(p.reference_price)})` : ""}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Contributions items={(p?.contributions ?? []) as { feature: string; value: unknown; effect_pct: number }[]} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Most similar past awards</CardTitle>
            <CardDescription>Closest in quantity, most recent first</CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <THead>
                <tr>
                  <TH>Date</TH>
                  <TH className="text-right">Qty</TH>
                  <TH className="text-right">Unit price</TH>
                  <TH>Awardee</TH>
                </tr>
              </THead>
              <TBody>
                {(p?.similar_awards ?? []).map((a, i) => {
                  const aw = a as { award_date: string; quantity: number; unit_price: number; awardee_cage: string | null };
                  return (
                    <TR key={i}>
                      <TD>{formatDate(aw.award_date)}</TD>
                      <TD className="num text-right">{num(aw.quantity)}</TD>
                      <TD className="num text-right">{money(aw.unit_price)}</TD>
                      <TD className="font-mono text-[12px]">{aw.awardee_cage ?? "—"}</TD>
                    </TR>
                  );
                })}
                {!(p?.similar_awards ?? []).length && (
                  <TR>
                    <TD colSpan={4} className="text-center text-xs text-muted-foreground">
                      No past awards.
                    </TD>
                  </TR>
                )}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Suppliers &amp; quotes</CardTitle>
          <CardDescription>
            Approved sources first, then past awardees, then your saved suppliers.
            {line.approved_source_only ? " Approved-source-only: only quotes for an approved part number count." : ""}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {line.approved_sources.length > 0 && (
            <div className="mb-2 text-xs text-muted-foreground">
              Approved sources:{" "}
              {line.approved_sources.map((s) => `${s.company_name ?? s.cage_code} (${s.cage_code}) P/N ${s.part_number}`).join(" · ")}
            </div>
          )}
          <SupplierPanel lineId={line.id} sn={sol.solicitation_number} candidates={line.candidates} voiceEnabled={voiceEnabled} demo={demo} />
        </CardContent>
      </Card>
    </section>
  );
}

function LinesSummary({ sol }: { sol: Sol }) {
  if (sol.lines.length < 2) return null;
  return (
    <Card className="mb-6" data-testid="lines-summary">
      <CardHeader>
        <CardTitle>{sol.lines.length} lines on this solicitation</CardTitle>
        <CardDescription>Each line is priced and quoted separately; jump to one below.</CardDescription>
      </CardHeader>
      <CardContent className="overflow-x-auto p-0">
        <Table>
          <THead>
            <tr>
              <TH>Line</TH>
              <TH>Item</TH>
              <TH className="text-right">Qty</TH>
              <TH className="text-right">Rec. bid</TH>
              <TH className="text-right">P(win)</TH>
              <TH>Status</TH>
            </tr>
          </THead>
          <TBody>
            {sol.lines.map((l) => {
              const r = l.recommendation;
              const ex = (r?.explanation ?? {}) as { needs_quote?: boolean; no_reference?: boolean };
              const status = !r ? "Not priced" : r.no_bid ? "No bid" : ex.no_reference ? "No reference" : ex.needs_quote ? "Quote first" : r.provisional ? "Bid (no quote yet)" : "Bid";
              return (
                <TR key={l.id}>
                  <TD>
                    <a className="text-primary hover:underline" href={`#line-${l.line_no}`}>
                      Line {l.line_no}
                    </a>
                  </TD>
                  <TD className="max-w-[260px] truncate" title={l.nomenclature ?? ""}>
                    {l.nomenclature}
                    <div className="font-mono text-[11px] text-muted-foreground">{formatNsn(l.nsn)}</div>
                  </TD>
                  <TD className="num text-right">
                    {num(l.quantity)} {l.unit_of_issue}
                  </TD>
                  <TD className="num text-right">{money(r?.recommended_bid)}</TD>
                  <TD className="num text-right">{pct(r?.p_win_at_recommended)}</TD>
                  <TD className={cn("text-[12px]", r?.no_bid && "text-muted-foreground")}>{status}</TD>
                </TR>
              );
            })}
          </TBody>
        </Table>
      </CardContent>
    </Card>
  );
}

export default function RfqPage() {
  const { solicitationNumber } = useParams<{ solicitationNumber: string }>();
  const sn = decodeURIComponent(solicitationNumber).toUpperCase();
  const { data: me } = useMe();
  const q = useQuery({
    queryKey: ["rfq", sn],
    queryFn: async () => unwrap(await api.GET("/api/rfq/{solicitation_number}", { params: { path: { solicitation_number: sn } } })),
  });
  return (
    <div>
      <Link href="/dashboard" className="mb-3 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-3.5 w-3.5" /> Today
      </Link>
      {q.isLoading ? (
        <Loading label="Loading solicitation…" />
      ) : q.error ? (
        <ErrorState error={q.error} />
      ) : q.data ? (
        <>
          <Header sol={q.data} />
          <LinesSummary sol={q.data} />
          <div className="space-y-10">
            {q.data.lines.map((line) => (
              <LineSection key={line.id} line={line} sol={q.data!} voiceEnabled={Boolean(me?.voice_globally_enabled)} demo={Boolean(me?.demo_mode)} />
            ))}
          </div>
        </>
      ) : null}
    </div>
  );
}
