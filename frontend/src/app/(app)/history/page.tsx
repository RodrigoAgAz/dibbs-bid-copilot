"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Empty, ErrorState, Loading, PageHeader, Stat } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Badge, Tip } from "@/components/ui/misc";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, unwrap } from "@/lib/api/client";
import { formatDate, formatNsn, money, num, pct, plural } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useDataHealth } from "@/lib/hooks";

const OUTCOME: Record<string, { label: string; variant: "success" | "destructive" | "secondary" | "outline" }> = {
  won: { label: "Won", variant: "success" },
  lost: { label: "Lost", variant: "destructive" },
  awarded: { label: "Awarded to another", variant: "outline" },
  pending: { label: "Awaiting award", variant: "secondary" },
  no_award_data: { label: "No award data", variant: "outline" },
};

const PAGE = 100;
const FILTERS = [
  { key: "all", label: "All" },
  { key: "decided", label: "Awarded" },
  { key: "bid", label: "You bid" },
  { key: "pending", label: "Awaiting award" },
] as const;
type FilterKey = (typeof FILTERS)[number]["key"];

export default function HistoryPage() {
  const q = useQuery({ queryKey: ["history"], queryFn: async () => unwrap(await api.GET("/api/history")) });
  const s = q.data?.stats;
  const [filter, setFilter] = useState<FilterKey>("all");
  const [shown, setShown] = useState(PAGE);
  const rows = useMemo(() => {
    const all = q.data?.rows ?? [];
    if (filter === "decided") return all.filter((r) => ["won", "lost", "awarded"].includes(r.outcome));
    if (filter === "bid") return all.filter((r) => r.actual_bid != null);
    if (filter === "pending") return all.filter((r) => r.outcome === "pending");
    return all;
  }, [q.data, filter]);
  const tr = useDataHealth().data?.live_track_record;
  return (
    <div>
      <PageHeader
        title="History"
        description="Past recommendations vs what you bid and who won, once awards post on DIBBS. Wins need your CAGE code on your organization."
      />
      {tr && tr.n > 0 && (
        <div className="mb-4 rounded-lg border bg-card p-4" data-testid="track-record">
          <div className="text-sm font-semibold">Live track record (all solicitations, not just yours)</div>
          <p className="mt-1 text-xs text-muted-foreground">
            Every solicitation priced before it was awarded, checked against the winning price once DLA posted the award. No hindsight: only
            the last prediction made before the award (and before the return-by date) counts, from the current pricing system
            {tr.since ? ` (since ${formatDate(tr.since)})` : ""}.
          </p>
          <div className="mt-3 grid grid-cols-3 gap-2 md:max-w-xl">
            <Stat label="Awarded lines checked" value={num(tr.n)} />
            <Stat label="Median price error" value={pct(tr.median_abs_pct_error)} />
            <Stat label="Winner inside 80% range" value={pct(tr.coverage_80)} tone="good" />
          </div>
        </div>
      )}
      {q.isLoading ? (
        <Loading />
      ) : q.error ? (
        <ErrorState error={q.error} />
      ) : !q.data?.rows.length ? (
        <Empty title="Nothing to show yet">Lines appear here after their return-by date, or as soon as you record what you bid.</Empty>
      ) : (
        <>
          <div className="mb-4 grid grid-cols-2 gap-2 md:grid-cols-5">
            <Stat label="Tracked lines" value={num(s!.tracked)} hint={`${num(s!.with_actual_bid)} with your bid recorded`} />
            <Stat label="Win rate" value={pct(s!.win_rate)} hint={`${num(s!.won)} won / ${num(s!.decided)} decided`} tone="good" />
            <Stat label="Realized margin" value={pct(s!.realized_margin_pct, 1)} hint="(bid − landed) / bid on wins" />
            <Stat label="Lost" value={num(s!.lost)} hint="lines you bid on that went to someone else" />
            <Stat
              label="Recommendation would win"
              value={s!.recommendation_decided ? pct(s!.recommendation_would_have_won / s!.recommendation_decided) : "—"}
              hint={`${num(s!.recommendation_would_have_won)} of ${plural(s!.recommendation_decided, "awarded line")} priced before the award (verified prices)`}
            />
          </div>
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            {FILTERS.map((f) => (
              <Button
                key={f.key}
                size="xs"
                variant={filter === f.key ? "default" : "outline"}
                onClick={() => {
                  setFilter(f.key);
                  setShown(PAGE);
                }}
              >
                {f.label}
              </Button>
            ))}
            <span className="ml-1 text-xs text-muted-foreground">{plural(rows.length, "line")}</span>
          </div>
          <div className="overflow-x-auto rounded-lg border bg-card shadow-sm">
            <Table>
              <THead>
                <tr>
                  <TH>Return by</TH>
                  <TH>Solicitation</TH>
                  <TH>NSN</TH>
                  <TH>Item</TH>
                  <TH className="text-right">Qty</TH>
                  <TH className="text-right">Landed</TH>
                  <TH className="text-right">Recommended</TH>
                  <TH className="text-right">You bid</TH>
                  <TH className="text-right">Winning price</TH>
                  <TH>Winner</TH>
                  <TH>Outcome</TH>
                </tr>
              </THead>
              <TBody>
                {rows.slice(0, shown).map((r) => {
                  const o = OUTCOME[r.outcome] ?? OUTCOME.no_award_data;
                  return (
                    <TR key={r.line_id}>
                      <TD>{formatDate(r.return_by_date)}</TD>
                      <TD className="font-mono text-[12px]">
                        <Link className="text-primary hover:underline" href={`/rfq/${r.solicitation_number}`}>
                          {r.solicitation_number}
                        </Link>
                      </TD>
                      <TD className="font-mono text-[12px]">{formatNsn(r.nsn)}</TD>
                      <TD className="max-w-[200px] truncate" title={r.nomenclature ?? ""}>
                        {r.nomenclature}
                      </TD>
                      <TD className="num text-right">{num(r.quantity)}</TD>
                      <TD className="num text-right">{money(r.landed_cost)}</TD>
                      <TD className="num text-right">
                        {r.no_bid ? (
                          <span className="text-muted-foreground">no bid</span>
                        ) : r.winning_price != null && r.recommended_bid != null && !r.counted && r.winning_price_verified ? (
                          <Tip content="Recomputed after the award posted: not counted in “Recommendation would win”">
                            <span className="text-muted-foreground">{money(r.recommended_bid)}*</span>
                          </Tip>
                        ) : (
                          money(r.recommended_bid)
                        )}
                      </TD>
                      <TD className="num text-right font-medium">{money(r.actual_bid)}</TD>
                      <TD className="num text-right">
                        {r.winning_price != null && !r.winning_price_verified ? (
                          <Tip content="Derived from a contract total and not backed by verified prices for this NSN: shown, not used in the stats">
                            <span className={cn("text-muted-foreground line-through decoration-1")}>{money(r.winning_price)}</span>
                          </Tip>
                        ) : (
                          money(r.winning_price)
                        )}
                      </TD>
                      <TD className="font-mono text-[12px]">{r.awardee_cage ?? "—"}</TD>
                      <TD>
                        <Badge variant={o.variant}>{o.label}</Badge>
                      </TD>
                    </TR>
                  );
                })}
              </TBody>
            </Table>
            {rows.length > shown && (
              <div className="border-t px-3 py-2 text-center">
                <Button variant="outline" size="xs" onClick={() => setShown((n) => n + PAGE)}>
                  Show {Math.min(PAGE, rows.length - shown)} more of {num(rows.length - shown)}
                </Button>
              </div>
            )}
          </div>
          <p className="mt-2 text-[11px] text-muted-foreground">
            * Recommendation recomputed after the award posted (not counted). Struck-through winning prices come from contract totals
            that don&apos;t match the NSN&apos;s verified prices.
          </p>
        </>
      )}
    </div>
  );
}
