"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, ChevronDown, ChevronRight, Download, RefreshCw, Search } from "lucide-react";
import { Empty, ErrorState, FlagBadges, Loading, PageHeader } from "@/components/common";
import { DemoGuide } from "@/components/demo-guide";
import { OnboardingChecklist } from "@/components/onboarding";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge, Tip } from "@/components/ui/misc";
import { NativeSelect } from "@/components/ui/overlay";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, unwrap, type Schemas } from "@/lib/api/client";
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

function DuePill({ line }: { line: DashboardLine }) {
  const c = countdown(line.days_left);
  return (
    <span
      className={cn(
        "num inline-flex rounded-full px-2 py-0.5 text-[11px] font-medium",
        c.tone === "danger" && "bg-destructive/10 text-destructive",
        c.tone === "warn" && "bg-warning/15 text-warning",
        c.tone !== "danger" && c.tone !== "warn" && "bg-muted text-muted-foreground",
      )}
    >
      {c.label}
    </span>
  );
}

/** The day at a glance: what bidding today's lines is worth, and what needs the broker. */
function TodayHero({
  s,
  awaiting,
  needsReview,
  onStatus,
}: {
  s: NonNullable<Schemas["DashboardOut"]["summary"]>;
  awaiting: number;
  needsReview: number;
  onStatus: (status: DashboardFilters["status"] | "needs_quote") => void;
}) {
  const items: { label: string; value: number; hint: string; tone?: "gold" | "warn"; onClick?: () => void }[] = [
    { label: "Worth bidding", value: s.bid_lines, hint: `of ${num(s.lines)} open lines`, tone: "gold" },
    { label: "Quote first", value: s.needs_quote_lines, hint: "no price history yet", onClick: () => onStatus("needs_quote") },
    { label: "Awaiting replies", value: awaiting, hint: "quote requests out", onClick: () => onStatus("sent") },
    { label: "Needs review", value: needsReview, hint: "parsed quotes to check", tone: needsReview ? "warn" : undefined, onClick: () => onStatus("needs_review") },
  ];
  return (
    <section
      className="relative mb-5 overflow-hidden rounded-xl bg-gradient-to-br from-ink-2 to-ink p-5 text-white shadow-sm sm:p-6"
      data-testid="today-hero"
    >
      <HandWatermark />
      <div className="relative grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)] lg:items-end">
        <div>
          <div className="text-[11px] font-medium uppercase tracking-[0.16em] text-white/60">Expected profit on today&apos;s bids</div>
          <div className="num mt-1 font-serif text-4xl text-gold sm:text-5xl">{money(s.total_expected_profit, { compact: true, digits: 0 })}</div>
          <div className="mt-2 text-xs text-white/70">
            {money(s.quoted_expected_profit ?? 0, { compact: true, digits: 0 })} backed by supplier quotes · the rest uses estimated costs until quotes
            come in
          </div>
        </div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {items.map((it) => {
            const body = (
              <>
                <div className="text-[10px] font-medium uppercase tracking-wider text-white/55">{it.label}</div>
                <div className={cn("num mt-0.5 text-2xl font-semibold", it.tone === "gold" && "text-gold", it.tone === "warn" && "text-amber-300")}>
                  {num(it.value)}
                </div>
                <div className="text-[11px] text-white/55">{it.hint}</div>
              </>
            );
            return it.onClick ? (
              <button
                key={it.label}
                type="button"
                onClick={it.onClick}
                className="rounded-lg border border-white/10 bg-white/5 px-3 py-2.5 text-left transition-colors hover:bg-white/10"
              >
                {body}
              </button>
            ) : (
              <div key={it.label} className="rounded-lg border border-white/10 bg-white/5 px-3 py-2.5">
                {body}
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

/** Faint open hand in the corner of the hero band. */
function HandWatermark() {
  return (
    <svg viewBox="0 0 24 24" className="pointer-events-none absolute -right-6 -top-4 h-56 w-56 text-gold opacity-[0.07]" aria-hidden>
      <g fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M18 11V6a2 2 0 0 0-2-2a2 2 0 0 0-2 2" />
        <path d="M14 10V4a2 2 0 0 0-2-2a2 2 0 0 0-2 2v2" />
        <path d="M10 10.5V6a2 2 0 0 0-2-2a2 2 0 0 0-2 2v8" />
        <path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15" />
      </g>
    </svg>
  );
}

/** The three lines most worth the broker's next hour. */
function BestBets({ lines }: { lines: DashboardLine[] }) {
  const top = lines.filter((l) => l.recommended_bid != null && !l.no_bid).slice(0, 3);
  if (!top.length) return null;
  return (
    <section className="mb-5" data-testid="best-bets">
      <div className="mb-2 flex items-baseline justify-between">
        <h2 className="font-serif text-lg">Best bets</h2>
        <span className="text-xs text-muted-foreground">expected profit, weighted by how well the price is known</span>
      </div>
      <div className="grid gap-3 md:grid-cols-3">
        {top.map((l, i) => (
          <Link
            key={l.line_id}
            href={`/rfq/${l.solicitation_number}`}
            className="group relative rounded-xl border bg-card p-4 shadow-sm transition-all hover:-translate-y-0.5 hover:border-gold/60 hover:shadow-md"
          >
            <div className="flex items-start justify-between gap-2">
              <span className="font-serif text-2xl leading-none text-gold">{i + 1}</span>
              <DuePill line={l} />
            </div>
            <div className="mt-2 truncate text-sm font-semibold" title={l.nomenclature ?? ""}>
              {l.nomenclature}
            </div>
            <div className="font-mono text-[11px] text-muted-foreground">
              {l.solicitation_number} · {num(l.quantity)} {l.unit_of_issue}
            </div>
            <div className="mt-3 flex items-end justify-between gap-2">
              <div>
                <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Bid</div>
                <div className="num text-xl font-semibold">{money(l.recommended_bid)}</div>
              </div>
              <div className="text-right">
                <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Expected profit</div>
                <div className="num text-xl font-semibold text-success">{money(l.expected_profit, { digits: 0 })}</div>
              </div>
            </div>
            <div className="mt-2 flex items-center justify-between text-xs text-muted-foreground">
              <PWin value={l.p_win} level={l.flags.confidence} />
              <span className="inline-flex items-center gap-0.5 text-primary opacity-0 transition-opacity group-hover:opacity-100">
                Open <ArrowUpRight className="h-3.5 w-3.5" />
              </span>
            </div>
          </Link>
        ))}
      </div>
    </section>
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

export default function DashboardPage() {
  const [filters, setFilters] = useState<DashboardFilters>(DEFAULT_FILTERS);
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
  // Same definitions as the quote-status filter, so the stats and the filtered lists always agree.
  const awaiting = allLines.filter((l) => statusOf(l) === "sent").length;
  const needsReview = allLines.filter((l) => statusOf(l) === "needs_review").length;

  const s = q.data?.summary;
  return (
    <div>
      <PageHeader
        title="Today"
        description={
          s ? (
            <>
              {formatDate(s.as_of)} · open DIBBS lines in your supply classes
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
      <OnboardingChecklist />
      <DemoGuide lines={allLines} />
      {s && (
        <TodayHero
          s={s}
          awaiting={awaiting}
          needsReview={needsReview}
          onStatus={(st) => {
            if (st === "needs_quote") update({ ...DEFAULT_FILTERS, flags: ["needs_quote"] });
            else update({ ...DEFAULT_FILTERS, status: st });
            tableTop.current?.scrollIntoView({ block: "start", behavior: "smooth" });
          }}
        />
      )}
      {!filtersActive && <BestBets lines={bid} />}

      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="font-serif text-lg">All lines</h2>
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
