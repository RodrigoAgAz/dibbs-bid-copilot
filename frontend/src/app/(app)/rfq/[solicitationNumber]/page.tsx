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
import { REFERENCE_LABELS, countdown, formatDate, formatNsn, formatSolicitation, isDemoSolicitation, modelLabel, money, num, pct, plural } from "@/lib/format";
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
            <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Expected profit</div>
            <div className={cn("num text-xl font-semibold", r.no_bid ? "text-muted-foreground" : "text-success")}>{money(r.expected_profit, { digits: 0 })}</div>
          </div>
          <div>
            <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Landed cost</div>
            <div className="num text-xl font-semibold">{money(r.landed_cost_per_unit)}</div>
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
          </div>
        ) : r.provisional && !r.no_bid && (
          <p className="rounded-md bg-muted px-2 py-1.5 text-xs text-muted-foreground">
            Provisional: no supplier quote yet. Landed cost is estimated from the predicted winning price. Send RFQ emails below to firm it up.
          </p>
        )}
        <div className="flex flex-wrap items-end justify-between gap-3 border-t pt-3">
          {r.no_bid ? <span /> : <CopyBid value={r.recommended_bid} />}
          <ActualBid line={line} sn={sn} />
        </div>
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
  };
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
            <CardDescription>Past awardees, most wins first</CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <THead>
                <tr>
                  <TH>Awardee</TH>
                  <TH className="text-right">Wins</TH>
                  <TH className="text-right">Last price</TH>
                  <TH>Last award</TH>
                </tr>
              </THead>
              <TBody>
                {competitors(history).map((c) => (
                  <TR key={c.cage}>
                    <TD className="max-w-[200px] truncate" title={c.name ?? ""}>
                      {c.name ?? <span className="text-muted-foreground">Unknown</span>}
                      <div className="font-mono text-[11px] text-muted-foreground">{c.cage === "unknown" ? "—" : c.cage}</div>
                    </TD>
                    <TD className="num text-right">{c.wins}</TD>
                    <TD className="num text-right">{c.lastPrice == null ? <span className="text-muted-foreground">unverified</span> : money(c.lastPrice)}</TD>
                    <TD>{formatDate(c.lastDate)}</TD>
                  </TR>
                ))}
                {!line.history.length && (
                  <TR>
                    <TD colSpan={4} className="text-center text-xs text-muted-foreground">
                      No past awards for this NSN.
                    </TD>
                  </TR>
                )}
              </TBody>
            </Table>
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
