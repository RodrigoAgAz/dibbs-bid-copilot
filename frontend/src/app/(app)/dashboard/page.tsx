"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  CheckCircle2,
  Clock,
  FileSearch,
  History,
  Inbox,
  ListChecks,
  MailQuestion,
  PackageSearch,
  Search,
  Settings,
  Sparkles,
  Users,
  type LucideIcon,
} from "lucide-react";
import { ErrorState, Loading } from "@/components/common";
import { DemoGuide } from "@/components/demo-guide";
import { OnboardingChecklist } from "@/components/onboarding";
import { api, unwrap } from "@/lib/api/client";
import { type DashboardLine } from "@/lib/dashboard";
import { formatDate, formatDateTime, formatNsn, money, num, plural } from "@/lib/format";
import { LANES, defaultLane, dueText, laneLines, orderValue, reasons, verdict, type LaneKey } from "@/lib/opportunities";
import { cn } from "@/lib/utils";

const LANE_ICON: Record<LaneKey, LucideIcon> = {
  closing: Clock,
  ready: CheckCircle2,
  quote: MailQuestion,
  review: Inbox,
  new: PackageSearch,
};
const SHOWN = 8;

/** The open book against the firm's limits: what is in flight, what the week's hours cover, where the capital sits. */
function PortfolioCard() {
  const q = useQuery({ queryKey: ["portfolio"], queryFn: async () => unwrap(await api.GET("/api/portfolio")) });
  const p = q.data;
  if (!p || (p.quoted_lines === 0 && p.pursue_candidates === 0)) return null;
  const capitalTone = p.capital_hold.length ? "text-warning" : "text-foreground";
  return (
    <section aria-labelledby="portfolio-heading" className="mb-8 rounded-xl border bg-card p-4" data-testid="portfolio">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="portfolio-heading" className="font-serif text-xl tracking-tight">
          Your book
        </h2>
        <Link href="/settings" className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
          <Settings className="h-3.5 w-3.5" aria-hidden /> Limits in Settings
        </Link>
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
        <div>
          <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">Quoted lines worth bidding</dt>
          <dd className="num text-lg font-semibold">{num(p.quoted_lines)}</dd>
          <dd className="text-xs text-muted-foreground">
            about {p.expected_awards.toFixed(1)} expected awards · {Math.round(p.p_no_award * 100)}% chance of none
          </dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">Expected capital in flight</dt>
          <dd className={cn("num text-lg font-semibold", capitalTone)}>{money(p.capital_in_flight, { digits: 0 })}</dd>
          <dd className="text-xs text-muted-foreground">
            {p.working_capital_limit != null ? `limit ${money(p.working_capital_limit, { digits: 0 })}` : "no limit set"}
            {p.capital_days > 0 ? ` · tied up about ${Math.round(p.capital_days)} days` : ""}
          </dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">Worth pursuing (no quote yet)</dt>
          <dd className="num text-lg font-semibold">{num(p.pursue_now.length)}</dd>
          <dd className="text-xs text-muted-foreground">
            {p.weekly_sourcing_hours != null
              ? `${p.pursuit_hours_needed.toFixed(1)} h needed, ${p.weekly_sourcing_hours} h available${p.deferred_capacity.length ? `: ${num(p.deferred_capacity.length)} wait` : ""}`
              : `about ${p.pursuit_hours_needed.toFixed(1)} h of sourcing`}
            {" · "}
            {money(p.pursuit_cost_committed, { digits: 0 })} of effort if none wins
          </dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">Expected profit (quoted)</dt>
          <dd className="num text-lg font-semibold text-success">{money(p.expected_profit, { digits: 0 })}</dd>
          <dd className="text-xs text-muted-foreground">
            {p.supplier_shares.length
              ? `top supplier ${p.supplier_shares[0].cage as string} carries ${Math.round((p.supplier_shares[0].share as number) * 100)}% of the capital`
              : "no supplier quotes in the book yet"}
          </dd>
        </div>
      </dl>
      {p.notes.length > 0 && (
        <ul className="mt-3 space-y-1 text-xs text-warning" data-testid="portfolio-notes">
          {p.notes.map((n) => (
            <li key={n}>{n.charAt(0).toUpperCase() + n.slice(1)}.</li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Jump to any open line by solicitation number, NSN or item name (keyboard: ↑ ↓ Enter Esc). */
function QuickFind({ lines }: { lines: DashboardLine[] }) {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const results = useMemo(() => {
    const t = q.trim().toLowerCase().replace(/-/g, "");
    if (t.length < 2) return [];
    return lines
      .filter((l) => `${l.solicitation_number} ${l.nsn} ${l.nomenclature ?? ""}`.toLowerCase().includes(t))
      .slice(0, 6);
  }, [q, lines]);
  useEffect(() => setActive(0), [q]);
  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  function go(l?: DashboardLine) {
    const sn = l?.solicitation_number ?? q.trim().toUpperCase().replace(/-/g, "");
    if (sn) router.push(`/rfq/${sn}`);
  }

  return (
    <div ref={box} className="relative">
      <label htmlFor="quick-find" className="sr-only">
        Find a solicitation, NSN or item
      </label>
      <Search className="pointer-events-none absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-muted-foreground" />
      <input
        id="quick-find"
        role="combobox"
        aria-expanded={open && results.length > 0}
        aria-controls="quick-find-results"
        aria-activedescendant={results.length ? `qf-${results[active]?.line_id}` : undefined}
        autoComplete="off"
        placeholder="Find a solicitation, NSN or item"
        className="h-12 w-full rounded-xl border bg-card pl-12 pr-4 text-base shadow-sm outline-none transition-shadow placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setActive((a) => Math.min(a + 1, results.length - 1));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((a) => Math.max(a - 1, 0));
          } else if (e.key === "Enter") {
            e.preventDefault();
            go(results[active]);
          } else if (e.key === "Escape") setOpen(false);
        }}
        data-testid="quick-find"
      />
      {open && results.length > 0 && (
        <ul id="quick-find-results" role="listbox" className="absolute z-20 mt-1.5 w-full overflow-hidden rounded-xl border bg-card py-1 shadow-lg">
          {results.map((l, i) => (
            <li
              key={l.line_id}
              id={`qf-${l.line_id}`}
              role="option"
              aria-selected={i === active}
              onMouseEnter={() => setActive(i)}
              onMouseDown={(e) => {
                e.preventDefault();
                go(l);
              }}
              className={cn("flex cursor-pointer items-center justify-between gap-3 px-4 py-2.5", i === active && "bg-accent")}
            >
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium">{l.nomenclature ?? "Unnamed item"}</span>
                <span className="block font-mono text-xs text-muted-foreground">
                  {l.solicitation_number} · NSN {formatNsn(l.nsn)}
                </span>
              </span>
              <span className="shrink-0 text-xs text-muted-foreground">{dueText(l.days_left)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function LaneTile({ lane, count, selected, onSelect }: { lane: (typeof LANES)[number]; count: number; selected: boolean; onSelect: () => void }) {
  const Icon = LANE_ICON[lane.key];
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      data-testid={`lane-${lane.key}`}
      className={cn(
        "group flex min-h-[112px] cursor-pointer flex-col rounded-xl border p-4 text-left transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        selected ? "border-ink bg-ink text-white shadow-md" : "bg-card hover:border-primary/40 hover:bg-accent/40",
      )}
    >
      <span className="flex items-center justify-between">
        <Icon className={cn("h-5 w-5", selected ? "text-gold" : "text-primary")} aria-hidden />
        <span className={cn("num text-2xl font-semibold", selected ? "text-gold" : count ? "text-foreground" : "text-muted-foreground")}>{num(count)}</span>
      </span>
      <span className="mt-3 text-sm font-semibold">{lane.title}</span>
      <span className={cn("mt-0.5 text-xs leading-snug", selected ? "text-white/70" : "text-muted-foreground")}>{lane.blurb}</span>
    </button>
  );
}

function DueBadge({ days }: { days: number | null | undefined }) {
  const urgent = days != null && days <= 1;
  const soon = days != null && days <= 3;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium",
        urgent ? "bg-destructive/10 text-destructive" : soon ? "bg-warning/15 text-warning" : "bg-muted text-muted-foreground",
      )}
    >
      <Clock className="h-3 w-3" aria-hidden /> {dueText(days)}
    </span>
  );
}

/** One opportunity in plain language: what it is, what to bid, why, and how big the order is. */
function OpportunityCard({ l }: { l: DashboardLine }) {
  const value = orderValue(l);
  return (
    <li>
      <Link
        href={`/rfq/${l.solicitation_number}`}
        className="group grid gap-4 rounded-xl border bg-card p-4 shadow-sm transition-colors duration-200 hover:border-primary/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:p-5 lg:grid-cols-[minmax(0,1fr)_220px]"
        data-testid="opportunity"
      >
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <DueBadge days={l.days_left} />
            <span className="font-mono text-xs text-muted-foreground">
              {l.solicitation_number} · NSN {formatNsn(l.nsn)}
            </span>
          </div>
          <h3 className="mt-2 truncate text-base font-semibold" title={l.nomenclature ?? ""}>
            {l.nomenclature ?? "Unnamed item"}{" "}
            <span className="font-normal text-muted-foreground">
              · {num(l.quantity)} {l.unit_of_issue ?? "units"}
            </span>
          </h3>
          <p className="mt-1 text-sm">{verdict(l)}</p>
          <ul className="mt-2 space-y-1">
            {reasons(l).map((r) => (
              <li key={r} className="flex gap-2 text-sm text-muted-foreground">
                <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-gold" aria-hidden />
                {r}
              </li>
            ))}
          </ul>
        </div>
        <div className="flex flex-row items-end justify-between gap-4 border-t pt-3 lg:flex-col lg:items-end lg:border-l lg:border-t-0 lg:pl-5 lg:pt-0">
          <dl className="grid grid-cols-2 gap-x-5 gap-y-2 text-left lg:grid-cols-1 lg:text-right">
            {l.predicted_median != null && (
              <div>
                <dt className="text-xs text-muted-foreground">Likely winning price</dt>
                <dd className="num text-base font-semibold">{money(l.predicted_median)}</dd>
              </div>
            )}
            {value > 0 && (
              <div>
                <dt className="text-xs text-muted-foreground">Order size</dt>
                <dd className="num text-base font-semibold">≈ {money(value, { compact: true, digits: 0 })}</dd>
              </div>
            )}
          </dl>
          <span className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap text-sm font-medium text-primary">
            Open analysis <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" aria-hidden />
          </span>
        </div>
      </Link>
    </li>
  );
}

const QUICK_LINKS: { href: string; label: string; icon: LucideIcon }[] = [
  { href: "/lines", label: "All lines", icon: ListChecks },
  { href: "/history", label: "Past bids and outcomes", icon: History },
  { href: "/suppliers", label: "Suppliers", icon: Users },
  { href: "/settings", label: "Margins and supply classes", icon: Settings },
];

function Home() {
  const router = useRouter();
  const params = useSearchParams();
  const q = useQuery({ queryKey: ["dashboard"], queryFn: async () => unwrap(await api.GET("/api/dashboard")) });
  const lines = useMemo(() => q.data?.lines ?? [], [q.data]);
  const byLane = useMemo(
    () => Object.fromEntries(LANES.map((l) => [l.key, laneLines(lines, l.key)])) as Record<LaneKey, DashboardLine[]>,
    [lines],
  );
  const counts = Object.fromEntries(LANES.map((l) => [l.key, byLane[l.key].length])) as Record<LaneKey, number>;
  const fromUrl = params.get("lane") as LaneKey | null;
  const [picked, setPicked] = useState<LaneKey | null>(fromUrl && LANES.some((l) => l.key === fromUrl) ? fromUrl : null);
  const laneKey = picked ?? defaultLane(counts);
  const lane = LANES.find((l) => l.key === laneKey)!;
  const shown = byLane[laneKey].slice(0, SHOWN);
  const s = q.data?.summary;
  const listHref = `/lines?${new URLSearchParams(lane.query as Record<string, string>).toString()}`;

  function select(k: LaneKey) {
    setPicked(k);
    router.replace(`/dashboard?lane=${k}`, { scroll: false });
  }

  return (
    <div className="mx-auto max-w-6xl">
      <header className="mb-5">
        <h1 className="font-serif text-3xl tracking-tight">Today</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {s ? (
            <>
              {formatDate(s.as_of)} · {plural(s.lines, "open DIBBS line")} in your supply classes, {num(s.bid_lines)} worth bidding
              {s.last_ingest_at ? ` · updated ${formatDateTime(s.last_ingest_at)}` : ""}
            </>
          ) : (
            "Open DIBBS lines in your supply classes"
          )}
        </p>
      </header>
      <OnboardingChecklist />
      <DemoGuide lines={lines} />

      <section aria-labelledby="nav-heading" className="mb-8 space-y-4">
        <h2 id="nav-heading" className="sr-only">
          Find your way
        </h2>
        <QuickFind lines={lines} />
        {q.isLoading ? (
          <Loading label="Loading today's solicitations…" />
        ) : q.error ? (
          <ErrorState error={q.error} />
        ) : (
          <>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5" role="group" aria-label="Where to start">
              {LANES.map((l) => (
                <LaneTile key={l.key} lane={l} count={counts[l.key]} selected={l.key === laneKey} onSelect={() => select(l.key)} />
              ))}
            </div>
            <nav aria-label="More" className="flex flex-wrap gap-2">
              {QUICK_LINKS.map(({ href, label, icon: Icon }) => (
                <Link
                  key={href}
                  href={href}
                  className="inline-flex min-h-[40px] items-center gap-2 rounded-lg border bg-card px-3 text-sm text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <Icon className="h-4 w-4" aria-hidden /> {label}
                </Link>
              ))}
            </nav>
          </>
        )}
      </section>

      {!q.isLoading && !q.error && <PortfolioCard />}

      {!q.isLoading && !q.error && (
        <section aria-labelledby="opps-heading" data-testid="opportunities">
          <div className="mb-3 flex flex-wrap items-end justify-between gap-2 border-b pb-3">
            <div>
              <h2 id="opps-heading" className="font-serif text-2xl tracking-tight">
                {lane.title}
              </h2>
              <p className="text-sm text-muted-foreground">
                {lane.blurb}. <span className="text-foreground">{lane.action}.</span>
              </p>
            </div>
            {counts[laneKey] > 0 && (
              <Link href={listHref} className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline">
                {counts[laneKey] > SHOWN ? `See all ${num(counts[laneKey])} in the full list` : "Open in the full list"}
                <ArrowRight className="h-4 w-4" aria-hidden />
              </Link>
            )}
          </div>
          {shown.length ? (
            <ul className="space-y-3">
              {shown.map((l) => (
                <OpportunityCard key={l.line_id} l={l} />
              ))}
            </ul>
          ) : (
            <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed py-12 text-center">
              {laneKey === "review" ? <Sparkles className="h-6 w-6 text-gold" aria-hidden /> : <FileSearch className="h-6 w-6 text-muted-foreground" aria-hidden />}
              <p className="text-sm font-medium">Nothing here right now</p>
              <p className="max-w-sm text-sm text-muted-foreground">Pick another lane above, or browse every open line in the full list.</p>
            </div>
          )}
        </section>
      )}
    </div>
  );
}

export default function HomePage() {
  return (
    <Suspense>
      <Home />
    </Suspense>
  );
}
