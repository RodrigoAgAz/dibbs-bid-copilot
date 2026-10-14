"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Empty, ErrorState, Loading, PageHeader, Stat } from "@/components/common";
import { Badge } from "@/components/ui/misc";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, unwrap } from "@/lib/api/client";
import { formatDate, formatNsn, money, num, pct } from "@/lib/format";
import { useDataHealth } from "@/lib/hooks";

const OUTCOME: Record<string, { label: string; variant: "success" | "destructive" | "secondary" | "outline" }> = {
  won: { label: "Won", variant: "success" },
  lost: { label: "Lost", variant: "destructive" },
  pending: { label: "Awaiting award", variant: "secondary" },
  no_award_data: { label: "No award data", variant: "outline" },
};

export default function HistoryPage() {
  const q = useQuery({ queryKey: ["history"], queryFn: async () => unwrap(await api.GET("/api/history")) });
  const s = q.data?.stats;
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
            Every solicitation priced before it closed, checked against the winning price once DLA posted the award. No hindsight: only the
            last prediction made before the return-by date counts.
          </p>
          <div className="mt-3 grid grid-cols-3 gap-2 md:max-w-xl">
            <Stat label="Awarded lines checked" value={num(tr.n)} hint={`last ${tr.since_days} days`} />
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
            <Stat label="Lost" value={num(s!.lost)} />
            <Stat
              label="Recommendation would win"
              value={s!.recommendation_decided ? pct(s!.recommendation_would_have_won / s!.recommendation_decided) : "—"}
              hint={`${num(s!.recommendation_would_have_won)} of ${num(s!.recommendation_decided)} awarded lines`}
            />
          </div>
          <div className="overflow-hidden rounded-lg border bg-card shadow-sm">
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
                {q.data.rows.map((r) => {
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
                      <TD className="max-w-[200px] truncate">{r.nomenclature}</TD>
                      <TD className="num text-right">{num(r.quantity)}</TD>
                      <TD className="num text-right">{money(r.landed_cost)}</TD>
                      <TD className="num text-right">{r.no_bid ? <span className="text-muted-foreground">no bid</span> : money(r.recommended_bid)}</TD>
                      <TD className="num text-right font-medium">{money(r.actual_bid)}</TD>
                      <TD className="num text-right">{money(r.winning_price)}</TD>
                      <TD className="font-mono text-[12px]">{r.awardee_cage ?? "—"}</TD>
                      <TD>
                        <Badge variant={o.variant}>{o.label}</Badge>
                      </TD>
                    </TR>
                  );
                })}
              </TBody>
            </Table>
          </div>
        </>
      )}
    </div>
  );
}
