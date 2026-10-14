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
import { countdown, formatDate, formatNsn, formatSolicitation, isDemoSolicitation, money, num, pct } from "@/lib/format";
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

function RecommendationCard({ line, sn }: { line: Line; sn: string }) {
  const qc = useQueryClient();
  const r = line.recommendation;
  const refresh = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/rfq/lines/{line_id}/refresh", { params: { path: { line_id: line.id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["rfq", sn] }),
  });
  if (!r) return null;
  const ex = (r.explanation ?? {}) as Record<string, unknown>;
  return (
    <Card data-testid="recommendation">
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <div>
          <CardTitle>Recommendation</CardTitle>
          <CardDescription>Maximizes P(win) × (bid − landed cost) × quantity</CardDescription>
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
                <li key={x}>{x}</li>
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
            <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Expected profit</div>
            <div className="num text-xl font-semibold text-success">{money(r.expected_profit, { digits: 0 })}</div>
          </div>
          <div>
            <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Landed cost</div>
            <div className="num text-xl font-semibold">{money(r.landed_cost_per_unit)}</div>
          </div>
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          <Tip content="Highest-margin bid that still wins at least 70% of the time">
            <span className="rounded-md border px-2 py-1">
              Conservative <b className="num">{money(r.conservative_bid)}</b>
              {ex.conservative_p_win != null && <span className="text-muted-foreground"> · {pct(ex.conservative_p_win as number)}</span>}
            </span>
          </Tip>
          <Tip content="Bid with about a 30% chance of winning">
            <span className="rounded-md border px-2 py-1">
              Aggressive <b className="num">{money(r.aggressive_bid)}</b>
              {ex.aggressive_p_win != null && <span className="text-muted-foreground"> · {pct(ex.aggressive_p_win as number)}</span>}
            </span>
          </Tip>
          <FlagBadges flags={{ provisional: r.provisional, low_data: Boolean(ex.low_data), cold_start: Boolean(ex.cold_start), approved_source_only: line.approved_source_only }} />
        </div>
        {r.provisional && (
          <p className="rounded-md bg-muted px-2 py-1.5 text-xs text-muted-foreground">
            Provisional: no supplier quote yet. Landed cost is estimated from the predicted winning price. Send RFQ emails below to firm it up.
          </p>
        )}
        <div className="flex flex-wrap items-end justify-between gap-3 border-t pt-3">
          <CopyBid value={r.recommended_bid} />
          <ActualBid line={line} sn={sn} />
        </div>
        <p className="text-[11px] text-muted-foreground">
          The Copilot never submits bids. Review the recommendation and submit your quote on DIBBS yourself.
        </p>
      </CardContent>
    </Card>
  );
}

function LineSection({ line, sol, voiceEnabled, demo }: { line: Line; sol: Sol; voiceEnabled: boolean; demo: boolean }) {
  const r = line.recommendation;
  const ex = (r?.explanation ?? {}) as { curve?: { bid: number; p_win: number; expected_profit: number }[] };
  const p = line.prediction;
  return (
    <section className="space-y-4" data-testid="line-section">
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
              P(win) ≈ P(winning price &gt; bid), from the predicted distribution
              {p ? ` (median ${money(p.median)}, ${p.model})` : ""}
            </CardDescription>
          </CardHeader>
          <CardContent className="pt-2">
            <PWinChart
              curve={ex.curve ?? []}
              landed={r?.landed_cost_per_unit ?? null}
              recommended={r?.recommended_bid ?? null}
              conservative={r?.conservative_bid ?? null}
              aggressive={r?.aggressive_bid ?? null}
            />
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Procurement history</CardTitle>
            <CardDescription>
              {line.history.length} past award{line.history.length === 1 ? "" : "s"} for this NSN · shaded band = predicted 10–90% range
            </CardDescription>
          </CardHeader>
          <CardContent className="pt-2">
            <HistoryChart awards={line.history} levels={p?.levels} prices={p?.prices} quantity={line.quantity} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Landed cost per unit</CardTitle>
            <CardDescription>{(r?.landed_breakdown as { basis?: string } | null)?.basis ?? ""}</CardDescription>
          </CardHeader>
          <CardContent className="pt-2">
            <WaterfallChart breakdown={r?.landed_breakdown as Record<string, number> | null} />
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
              Feature effects on the predicted median vs the reference price
              {p?.reference_price ? ` (${p.reference_type?.replace("_", " ")} ${money(p.reference_price)})` : ""}
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
