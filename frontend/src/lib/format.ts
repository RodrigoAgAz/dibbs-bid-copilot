/** Formatting helpers (pure, unit-tested). */

export function money(v: number | null | undefined, opts: { compact?: boolean; digits?: number } = {}): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const abs = Math.abs(v);
  if (opts.compact && abs >= 10_000) {
    return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 }).format(v);
  }
  const digits = opts.digits ?? (abs > 0 && abs < 1 ? 4 : 2);
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: Math.min(2, digits),
    maximumFractionDigits: digits,
  }).format(v);
}

export function pct(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

export function num(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: digits }).format(v);
}

/** 5305011209897 -> 5305-01-120-9897 (leaves non-standard values alone). */
export function formatNsn(nsn: string | null | undefined): string {
  if (!nsn) return "—";
  const n = nsn.replace(/-/g, "");
  if (n.length !== 13) return nsn;
  return `${n.slice(0, 4)}-${n.slice(4, 6)}-${n.slice(6, 9)}-${n.slice(9)}`;
}

/** SPE4A627T0486 -> SPE4A6-27-T-0486 */
export function formatSolicitation(sn: string | null | undefined): string {
  if (!sn) return "—";
  const s = sn.replace(/-/g, "");
  if (s.length !== 13) return sn;
  return `${s.slice(0, 6)}-${s.slice(6, 8)}-${s.slice(8, 9)}-${s.slice(9)}`;
}

export function isDemoSolicitation(sn: string | null | undefined): boolean {
  return Boolean(sn && sn.replace(/-/g, "").toUpperCase().startsWith("SPEZZ"));
}

export function formatDate(d: string | null | undefined): string {
  if (!d) return "—";
  const date = new Date(d.length === 10 ? `${d}T12:00:00` : d);
  if (Number.isNaN(date.getTime())) return d;
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

export function formatDateTime(d: string | null | undefined): string {
  if (!d) return "—";
  const date = new Date(d);
  if (Number.isNaN(date.getTime())) return d;
  return date.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

/** Countdown label for a due date in days. */
export function countdown(daysLeft: number | null | undefined): { label: string; tone: "danger" | "warn" | "normal" | "muted" } {
  if (daysLeft === null || daysLeft === undefined) return { label: "—", tone: "muted" };
  if (daysLeft < 0) return { label: "closed", tone: "muted" };
  if (daysLeft === 0) return { label: "today", tone: "danger" };
  if (daysLeft === 1) return { label: "1 day", tone: "danger" };
  if (daysLeft <= 3) return { label: `${daysLeft} days`, tone: "warn" };
  return { label: `${daysLeft} days`, tone: "normal" };
}

export const FSC_NAMES: Record<string, string> = {
  "5305": "Screws",
  "5306": "Bolts",
  "5310": "Nuts & washers",
  "5330": "Packing & gaskets",
  "5340": "Hardware",
  "4730": "Hose, pipe & tube fittings",
  "6145": "Wire & cable",
};

export function fscLabel(fsc: string): string {
  return FSC_NAMES[fsc] ? `${fsc} ${FSC_NAMES[fsc]}` : fsc;
}

export const FEATURE_LABELS: Record<string, string> = {
  log_quantity: "Quantity",
  qty_rel_median: "Quantity vs typical",
  log_qty_vs_last: "Quantity vs last award",
  n_prior_awards: "Number of past awards",
  days_since_last_award: "Days since last award",
  f_last3_median: "Recent award prices",
  f_min: "Lowest past price",
  f_max: "Highest past price",
  volatility: "Price volatility",
  trend_per_year: "Price trend",
  n_distinct_awardees: "Number of past winners",
  incumbent_share: "Incumbent strength",
  approved_source_only: "Approved source only",
  n_approved_sources: "Approved sources",
  fast_award: "Fast award",
  aidc: "AIDC",
  first_article: "First article",
  log_last_to_dod: "Last price vs DoD standard",
  inflation_change: "Inflation since last award",
  log_ref_price: "Price level",
  fsc: "FSC",
  unit_of_issue: "Unit of issue",
  buyer_prefix: "Buying activity",
  amsc: "AMSC",
  type_code: "Solicitation type",
  set_aside: "Set-aside",
  inspection_point: "Inspection point",
  fob: "FOB",
  ref_type: "Reference price type",
};

/** "just now", "12 min ago", "3 h ago", "2 days ago" (null -> "never"). */
export function timeAgo(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "never";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const mins = Math.max(0, Math.round((now.getTime() - t) / 60_000));
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}
