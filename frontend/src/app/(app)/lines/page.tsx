"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Download, RefreshCw, Search } from "lucide-react";
import { Empty, ErrorState, FlagBadges, Loading, PageHeader } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge, Tip } from "@/components/ui/misc";
import { NativeSelect } from "@/components/ui/overlay";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, unwrap } from "@/lib/api/client";
import { filtersFromQuery } from "@/lib/opportunities";
import { DEFAULT_FILTERS, filterLines, sortLines, splitNoBid, statusOf, type DashboardFilters, type DashboardLine, type FlagKey, type SortKey } from "@/lib/dashboard";
import { countdown, formatDate, formatDateTime, formatNsn, fscLabel, money, num, pct } from "@/lib/format";
import { downloadCsv, linesToCsv } from "@/lib/csv";
import { cn } from "@/lib/utils";

const PAGE = 100;
const TYPE_LABELS: Record<string, string> = {
  T: "Automated solicitation (T): priced and awarded automatically",
  U: "Automated IDC / AIDC (U): a long-term contract",
  Q: "Manual solicitation (Q): reviewed by a buyer",
};
const NO_BID_PAGE = 100;
const FLAG_OPTIONS: { key: FlagKey; label: string }[] = [
  { key: "fast_award", label: "Fast award" },
  { key: "approved_source_only", label: "Approved source only" },
  { key: "low_data", label: "Low data" },
  { key: "provisional", label: "Provisional" },
  { key: "needs_quote", label: "Quote first" },
];

const STATUS_LABEL: Record<ReturnType<typeof statusOf>, string> = {
  not_started: "Not started",
  drafts: "Drafts",
  sent: "Awaiting reply",
  quoted: "Quoted",
  needs_review: "Needs review",
};

function DueCell({ line }: { line: DashboardLine }) {
  const c = countdown(line.days_left);
  return (
    <div className="leading-tight">
      <div
        className={cn(
          "num text-[12px] font-medium",
          c.tone === "danger" && "text-destructive",
          c.tone === "warn" && "text-warning",
          c.tone === "muted" && "text-muted-foreground",
        )}
      >
        {c.label}
      </div>
      <div className="text-[11px] text-muted-foreground">{formatDate(line.return_by_date)}</div>
    </div>
  );
}

function QuoteCell({ line }: { line: DashboardLine }) {
  const s = statusOf(line);
  const variant = s === "quoted" ? "success" : s === "needs_review" ? "warning" : s === "sent" ? "default" : "secondary";
  return (
    <Tip content={`${line.quote_status.candidates} candidate supplier(s): ${line.quote_status.label}`}>
      <span>
        <Badge variant={variant}>{STATUS_LABEL[s]}</Badge>
      </span>
    </Tip>
  );
}

const CONFIDENCE: Record<string, { cls: string; label: string }> = {
  high: { cls: "bg-success", label: "High confidence: several past awards and a tight predicted range" },
  medium: { cls: "bg-warning", label: "Medium confidence: few past awards or a wider range" },
  low: { cls: "bg-destructive/70", label: "Low confidence: one past award or a very wide range; check the history" },
  none: { cls: "bg-muted-foreground/40", label: "No price history for this NSN: get a supplier quote first" },
};

/** P(win) as a number with a thin bar (colored by confidence). */
function PWin({ value, level }: { value: number | null | undefined; level?: string | null }) {
  if (value == null) return <span className="text-muted-foreground">—</span>;
  const c = CONFIDENCE[level ?? "low"] ?? CONFIDENCE.low;
  return (
    <span className="inline-flex items-center justify-end gap-2">
      <span className="num w-9 text-right">{pct(value)}</span>
      <Tip content={c.label}>
        <span className="relative h-1.5 w-12 overflow-hidden rounded-full bg-muted" data-testid="confidence-dot" aria-label={c.label}>
          <span className={cn("absolute inset-y-0 left-0 rounded-full", c.cls)} style={{ width: `${Math.round(value * 100)}%` }} />
        </span>
      </Tip>
    </span>
  );
}

function ConfidenceDot({ level }: { level?: string | null }) {
  const c = CONFIDENCE[level ?? "low"] ?? CONFIDENCE.low;
  return (
    <Tip content={c.label}>
      <span className={cn("inline-block h-2 w-2 rounded-full", c.cls)} aria-label={c.label} data-testid="confidence-dot" />
    </Tip>
  );
}

/** Phone layout: one card per line with the numbers that matter (the table needs ~1,100px). */
function LineCards({ lines, noBid }: { lines: DashboardLine[]; noBid?: boolean }) {
  return (
    <ul className="divide-y md:hidden" data-testid="rfq-cards">
      {lines.map((l) => {
        const c = countdown(l.days_left);
        return (
          <li key={l.line_id}>
            <Link href={`/rfq/${l.solicitation_number}`} className={cn("block px-3 py-2.5 hover:bg-accent/50", noBid && "text-muted-foreground")}>
              <div className="flex items-baseline justify-between gap-2">
                <span className="font-mono text-[12px] text-primary">{l.solicitation_number}</span>
                <span
                  className={cn(
                    "num text-[12px] font-medium",
                    c.tone === "danger" && "text-destructive",
                    c.tone === "warn" && "text-warning",
                  )}
                >
                  {c.label}
                </span>
              </div>
              <div className="truncate text-sm">{l.nomenclature}</div>
              <div className="text-[11px] text-muted-foreground">
                {num(l.quantity)} {l.unit_of_issue} · NSN {formatNsn(l.nsn)}
              </div>
              {noBid ? (
                <div className="mt-1 line-clamp-2 text-[12px]">{l.no_bid_reason}</div>
              ) : (
                <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px]">
                  <span>
                    Bid <b className="num">{money(l.recommended_bid)}</b>
                  </span>
                  <span className="inline-flex items-center gap-1">
                    {pct(l.p_win)} <ConfidenceDot level={l.flags.confidence} />
                  </span>
                  <span className={cn("num", (l.expected_profit ?? 0) > 0 && "text-success")}>{money(l.expected_profit, { digits: 0 })}</span>
                  <QuoteCell line={l} />
                  <FlagBadges flags={l.flags} compact />
                </div>
              )}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

function LinesTable({ lines, noBid }: { lines: DashboardLine[]; noBid?: boolean }) {
  const router = useRouter();
  return (
    <>
    <LineCards lines={lines} noBid={noBid} />
    <Table className="hidden md:table">
      <THead>
        <tr>
          <TH className="pl-4">Due</TH>
          <TH className="min-w-[260px]">Item</TH>
          <TH className="text-right">Qty</TH>
          <TH className="text-right">Pred. median</TH>
          <TH className="text-right">Landed</TH>
          <TH className="text-right">Rec. bid</TH>
          <TH className="text-right">P(win)</TH>
          <TH className="text-right">Exp. profit</TH>
          <TH>Quotes</TH>
          {noBid ? <TH>No-bid reason</TH> : <TH>Flags</TH>}
        </tr>
      </THead>
      <TBody>
        {lines.map((l) => (
          <TR
            key={l.line_id}
            className={cn("cursor-pointer", noBid && "text-muted-foreground")}
            onClick={() => router.push(`/rfq/${l.solicitation_number}`)}
            data-testid="rfq-row"
          >
            <TD className="py-2 pl-4">
              <DueCell line={l} />
            </TD>
            <TD className="max-w-[340px] py-2">
              <div className="truncate font-medium" title={l.nomenclature ?? ""}>
                {l.nomenclature ?? <span className="text-muted-foreground">Unnamed item</span>}
              </div>
              <div className="font-mono text-[11px] text-muted-foreground">
                <Link href={`/rfq/${l.solicitation_number}`} className="text-primary hover:underline" onClick={(e) => e.stopPropagation()}>
                  {l.solicitation_number}
                </Link>
                {l.type_code && (
                  <Tip content={TYPE_LABELS[l.type_code] ?? `Solicitation type ${l.type_code}`}>
                    <span className="ml-1 cursor-help">{l.type_code}</span>
                  </Tip>
                )}
                <span className="mx-1.5">·</span>NSN {formatNsn(l.nsn)}
              </div>
            </TD>
            <TD className="num text-right">
              {num(l.quantity)} <span className="text-[10px] text-muted-foreground">{l.unit_of_issue}</span>
            </TD>
            <TD className="num text-right">{money(l.predicted_median)}</TD>
            <TD className="num text-right">{money(l.landed_cost)}</TD>
            <TD className="num text-right font-semibold">{noBid ? <span className="font-normal">{money(l.recommended_bid)}</span> : money(l.recommended_bid)}</TD>
            <TD className="num text-right">
              <PWin value={l.p_win} level={l.flags.confidence} />
            </TD>
            <TD className={cn("num text-right", !noBid && (l.expected_profit ?? 0) > 0 && "text-success font-medium")}>
              {money(l.expected_profit, { digits: 0 })}
            </TD>
            <TD>
              <QuoteCell line={l} />
            </TD>
            {noBid ? (
              <TD className="max-w-[320px] truncate text-[12px]" title={l.no_bid_reason ?? ""}>
                {l.no_bid_reason}
              </TD>
            ) : (
              <TD>
                <FlagBadges flags={l.flags} compact />
              </TD>
            )}
          </TR>
        ))}
      </TBody>
    </Table>
    </>
  );
}

function LinesPage() {
  const params = useSearchParams();
  const [filters, setFilters] = useState<DashboardFilters>(() => filtersFromQuery(new URLSearchParams(params.toString()), DEFAULT_FILTERS));
  const [page, setPage] = useState(0);
  const [showNoBid, setShowNoBid] = useState(false);
  const [noBidShown, setNoBidShown] = useState(NO_BID_PAGE);
  const tableTop = useRef<HTMLDivElement>(null);
  const [sort, setSort] = useState<SortKey>("best");
  const q = useQuery({ queryKey: ["dashboard"], queryFn: async () => unwrap(await api.GET("/api/dashboard")) });

  const allLines = useMemo(() => q.data?.lines ?? [], [q.data]);
  const fscs = useMemo(() => Array.from(new Set(allLines.map((l) => l.fsc))).sort(), [allLines]);
  const filtered = useMemo(() => filterLines(allLines, filters), [allLines, filters]);
  const { bid, noBid } = useMemo(() => splitNoBid(sortLines(filtered, sort)), [filtered, sort]);
  const pages = Math.max(1, Math.ceil(bid.length / PAGE));
  const pageLines = bid.slice(page * PAGE, (page + 1) * PAGE);

  function update(f: Partial<DashboardFilters>) {
    setFilters((cur) => ({ ...cur, ...f }));
    setPage(0);
    setNoBidShown(NO_BID_PAGE);
  }
  function goPage(p: number) {
    setPage(p);
    tableTop.current?.scrollIntoView({ block: "start", behavior: "smooth" });
  }
  const filtersActive = JSON.stringify(filters) !== JSON.stringify(DEFAULT_FILTERS);

  const s = q.data?.summary;
  return (
    <div>
      <PageHeader
        title="All lines"
        description={
          s ? (
            <>
              Every open DIBBS line in your supply classes · {formatDate(s.as_of)}
              {s.last_ingest_at ? ` · updated ${formatDateTime(s.last_ingest_at)}` : ""}
            </>
          ) : (
            "Open DIBBS lines in your supply classes"
          )
        }
        actions={
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={!filtered.length}
              onClick={() => downloadCsv(`dibbs-lines-${s?.as_of ?? "today"}.csv`, linesToCsv([...bid, ...noBid], window.location.origin))}
              data-testid="export-csv"
            >
              <Download /> Export CSV
            </Button>
            <Button variant="outline" size="sm" onClick={() => q.refetch()} disabled={q.isFetching}>
              <RefreshCw className={cn(q.isFetching && "animate-spin")} /> Refresh
            </Button>
          </div>
        }
      />
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="sr-only">Lines</h2>
        <span className="text-xs text-muted-foreground">
          {num(bid.length)} line{bid.length === 1 ? "" : "s"}{noBid.length ? ` · ${num(noBid.length)} no-bid below` : ""}
        </span>
      </div>
      <div className="mb-3 flex flex-wrap items-center gap-2 rounded-xl border bg-card p-2.5 shadow-sm">
        <div className="relative min-w-[220px] flex-1 sm:max-w-sm">
          <Search className="pointer-events-none absolute left-2.5 top-2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search solicitation, NSN, item…"
            className="h-8 w-full pl-8 text-xs"
            value={filters.search}
            onChange={(e) => update({ search: e.target.value })}
            aria-label="Search"
          />
        </div>
        <NativeSelect aria-label="FSC" value={filters.fscs[0] ?? ""} onChange={(e) => update({ fscs: e.target.value ? [e.target.value] : [] })}>
          <option value="">All FSCs</option>
          {fscs.map((f) => (
            <option key={f} value={f}>
              {fscLabel(f)}
            </option>
          ))}
        </NativeSelect>
        <NativeSelect
          aria-label="Due within"
          value={filters.dueWithin ?? ""}
          onChange={(e) => update({ dueWithin: e.target.value ? Number(e.target.value) : null })}
        >
          <option value="">Any due date</option>
          <option value="1">Due ≤ 1 day</option>
          <option value="3">Due ≤ 3 days</option>
          <option value="7">Due ≤ 7 days</option>
          <option value="14">Due ≤ 14 days</option>
        </NativeSelect>
        <NativeSelect aria-label="Quote status" value={filters.status} onChange={(e) => update({ status: e.target.value as DashboardFilters["status"] })}>
          <option value="all">Any quote status</option>
          {Object.entries(STATUS_LABEL).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </NativeSelect>
        <NativeSelect aria-label="Sort" value={sort} onChange={(e) => (setSort(e.target.value as SortKey), setPage(0))}>
          <option value="best">Sort: best bets</option>
          <option value="expected_profit">Sort: expected profit</option>
          <option value="due">Sort: due soonest</option>
          <option value="line_value">Sort: line value</option>
          <option value="p_win">Sort: P(win)</option>
        </NativeSelect>
        <div className="flex flex-wrap items-center gap-1.5">
          {FLAG_OPTIONS.map((f) => {
            const on = filters.flags.includes(f.key);
            return (
              <button
                key={f.key}
                type="button"
                aria-pressed={on}
                onClick={() => update({ flags: on ? filters.flags.filter((x) => x !== f.key) : [...filters.flags, f.key] })}
                className={cn(
                  "rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors",
                  on ? "border-primary bg-primary text-primary-foreground" : "bg-card text-muted-foreground hover:border-primary/40 hover:text-foreground",
                )}
              >
                {f.label}
              </button>
            );
          })}
        </div>
        {filtersActive && (
          <Button variant="ghost" size="xs" onClick={() => update(DEFAULT_FILTERS)}>
            Clear
          </Button>
        )}
      </div>

      {q.isLoading ? (
        <Loading label="Loading today's RFQs…" />
      ) : q.error ? (
        <ErrorState error={q.error} />
      ) : !allLines.length ? (
        <Empty title="No open RFQ lines match your watchlist">
          Lines appear after the daily ingest (06:00 ET). Check your FSC watchlist in Settings, or run <code>make seed</code> for demo data.
        </Empty>
      ) : (
        <>
          <div ref={tableTop} className="scroll-mt-20 overflow-hidden rounded-xl border bg-card shadow-sm" data-testid="rfq-table">
            {pageLines.length ? <LinesTable lines={pageLines} /> : <div className="p-4"><Empty title="No bid-worthy lines match these filters" /></div>}
            {pages > 1 && (
              <div className="flex items-center justify-between border-t px-3 py-2 text-xs text-muted-foreground">
                <span>
                  {page * PAGE + 1}–{Math.min((page + 1) * PAGE, bid.length)} of {bid.length}
                </span>
                <div className="flex gap-1">
                  <Button variant="outline" size="xs" disabled={page === 0} onClick={() => goPage(page - 1)}>
                    Previous
                  </Button>
                  <Button variant="outline" size="xs" disabled={page >= pages - 1} onClick={() => goPage(page + 1)}>
                    Next
                  </Button>
                </div>
              </div>
            )}
          </div>
          {noBid.length > 0 && (
            <div className="mt-4">
              <button
                className="mb-2 flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground"
                onClick={() => setShowNoBid((v) => !v)}
                aria-expanded={showNoBid}
              >
                {showNoBid ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                {noBid.length} no-bid line{noBid.length === 1 ? "" : "s"}
              </button>
              {showNoBid && (
                <div className="overflow-hidden rounded-xl border bg-card shadow-sm">
                  <LinesTable lines={noBid.slice(0, noBidShown)} noBid />
                  {noBid.length > noBidShown && (
                    <div className="border-t px-3 py-2 text-center">
                      <Button variant="outline" size="xs" onClick={() => setNoBidShown((n) => n + NO_BID_PAGE)}>
                        Show {Math.min(NO_BID_PAGE, noBid.length - noBidShown)} more of {num(noBid.length - noBidShown)}
                      </Button>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default function LinesPageRoute() {
  return (
    <Suspense>
      <LinesPage />
    </Suspense>
  );
}
