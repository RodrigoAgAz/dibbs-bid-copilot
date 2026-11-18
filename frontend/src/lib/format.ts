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

/** The five weapon-system categories the product watches by default: real DLA supply classes for parts of each. */
export const CATEGORIES: { fsc: string; title: string; blurb: string }[] = [
  { fsc: "2530", title: "Tanks & combat vehicles", blurb: "Track shoes, road wheels, sprockets, brakes" },
  { fsc: "1005", title: "Rifles & machine guns", blurb: "Bolt carriers, barrels, receivers, triggers" },
  { fsc: "1010", title: "Grenade launchers", blurb: "40 mm feed trays, barrels, sights, mounts" },
  { fsc: "1055", title: "Rocket launchers", blurb: "Launch tubes, firing mechanisms, smoke dischargers" },
  { fsc: "5855", title: "Night vision", blurb: "Intensifier tubes, monoculars, thermal sights" },
];

export const FSC_NAMES: Record<string, string> = {
  "1005": "Rifles & machine guns",
  "1010": "Grenade launchers (guns 30–75 mm)",
  "1055": "Rocket launchers",
  "1560": "Airframe structural components",
  "2530": "Tanks & combat vehicles: track, wheels & brakes",
  "2540": "Vehicular furniture & accessories",
  "2590": "Misc. vehicular components",
  "2805": "Gasoline reciprocating engines",
  "2815": "Diesel engines & components",
  "2835": "Gas turbines & jet engines (non-aircraft)",
  "2840": "Gas turbines & jet engines (aircraft)",
  "2910": "Engine fuel system components",
  "2915": "Aircraft engine fuel system components",
  "2920": "Engine electrical system components",
  "2930": "Engine cooling system components",
  "2940": "Engine air & oil filters",
  "2990": "Misc. engine accessories",
  "2995": "Misc. aircraft engine accessories",
  "3010": "Torque converters & speed changers",
  "3020": "Gears, pulleys, sprockets & chain",
  "3030": "Belting, drive belts & fan belts",
  "3040": "Misc. power transmission equipment",
  "3110": "Bearings, antifriction",
  "3120": "Bearings, plain",
  "4310": "Compressors & vacuum pumps",
  "4320": "Power & hand pumps",
  "4330": "Centrifugals, separators & filters",
  "4710": "Pipe, tube & rigid tubing",
  "4720": "Hose & flexible tubing",
  "4730": "Hose, pipe & tube fittings",
  "4810": "Valves, powered",
  "4820": "Valves, nonpowered",
  "5120": "Hand tools, nonedged",
  "5305": "Screws",
  "5306": "Bolts",
  "5307": "Studs",
  "5310": "Nuts & washers",
  "5315": "Nails, keys & pins",
  "5320": "Rivets",
  "5325": "Fastening devices",
  "5330": "Packing & gaskets",
  "5331": "O-rings",
  "5335": "Metal screening",
  "5340": "Hardware",
  "5355": "Knobs & pointers",
  "5360": "Coil, flat & wire springs",
  "5365": "Rings, shims & spacers",
  "5855": "Night vision & thermal sights",
  "5970": "Electrical insulators",
  "5975": "Electrical hardware & supplies",
  "5925": "Circuit breakers",
  "5930": "Switches",
  "5935": "Connectors, electrical",
  "5940": "Lugs, terminals & terminal strips",
  "5945": "Relays & solenoids",
  "5961": "Semiconductor devices",
  "5962": "Microcircuits, electronic",
  "5995": "Cable, cord & wire assemblies",
  "5999": "Misc. electrical & electronic components",
  "6105": "Motors, electrical",
  "6110": "Electrical control equipment",
  "6130": "Converters, electrical",
  "6140": "Batteries, rechargeable",
  "6145": "Wire & cable",
  "6150": "Misc. electric power distribution equipment",
  "6210": "Indoor & outdoor electric lighting fixtures",
  "6220": "Electric vehicular lights & fixtures",
  "6240": "Electric lamps",
  "6685": "Pressure, temperature & humidity instruments",
  "8030": "Preservative & sealing compounds",
  "8040": "Adhesives",
  "9150": "Oils & greases",
  "9320": "Rubber fabricated materials",
  "9330": "Plastic fabricated materials",
  "9390": "Misc. fabricated nonmetallic materials",
  "9515": "Plate, sheet, strip & foil (iron & steel)",
  "9530": "Bars & rods (nonferrous)",
  "9535": "Plate, sheet, strip & foil (nonferrous)",
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
  log_ref_price: "Reference price",
  fsc: "Supply class",
  unit_of_issue: "Unit of issue",
  buyer_prefix: "Buying activity",
  amsc: "AMSC",
  type_code: "Solicitation type",
  set_aside: "Set-aside",
  inspection_point: "Inspection point",
  fob: "FOB",
  ref_type: "Reference price type",
  niin_country: "Catalog country",
  fam_log_ref_vs_fsc: "Similar parts' price level",
  fam_n: "Similar parts found",
  fam_spread: "Similar parts' price spread",
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

/** "1 award", "3 awards", "1,204 lines". */
export function plural(n: number, word: string, pluralWord?: string): string {
  return `${num(n)} ${n === 1 ? word : (pluralWord ?? `${word}s`)}`;
}

export const MODEL_LABELS: Record<string, string> = {
  lgbm_quantile_v2: "pricing model v2",
  lgbm_quantile_v1: "pricing model v1",
  heuristic_v0: "rule-of-thumb estimate",
};

export function modelLabel(m: string | null | undefined): string {
  return m ? (MODEL_LABELS[m] ?? m.replace(/_/g, " ")) : "";
}

export const REFERENCE_LABELS: Record<string, string> = {
  last_award: "inflation-adjusted last award",
  dod_price: "DoD standard price",
  fsc_median: "supply-class median price",
  similar_items: "similar parts' prices",
  none: "no reference",
};

const ratio = (v: number) => `${v >= 0 ? "+" : "−"}${Math.round(Math.abs(Math.expm1(v)) * 100)}%`;
const yesNo = (v: number) => (v >= 0.5 ? "yes" : "no");
const FEATURE_VALUE: Record<string, (v: number) => string> = {
  log_quantity: (v) => num(Math.round(Math.expm1(v))),
  qty_rel_median: (v) => `${num(v, v < 10 ? 1 : 0)}x typical`,
  log_qty_vs_last: (v) => `${num(Math.exp(v), Math.exp(v) < 10 ? 1 : 0)}x last`,
  n_prior_awards: (v) => num(v),
  n_distinct_awardees: (v) => num(v),
  n_approved_sources: (v) => num(v),
  days_since_last_award: (v) => plural(Math.round(v), "day"),
  f_last3_median: (v) => `${ratio(v)} vs last`,
  f_min: (v) => `${ratio(v)} vs last`,
  f_max: (v) => `${ratio(v)} vs last`,
  volatility: (v) => `±${Math.round(Math.expm1(v) * 100)}%`,
  trend_per_year: (v) => `${ratio(v)}/yr`,
  incumbent_share: (v) => pct(v),
  approved_source_only: yesNo,
  fast_award: yesNo,
  aidc: yesNo,
  first_article: yesNo,
  log_last_to_dod: (v) => `${num(Math.exp(v), 2)}x DoD price`,
  inflation_change: ratio,
  log_ref_price: (v) => money(Math.exp(v)),
  fam_log_ref_vs_fsc: (v) => `${num(Math.exp(v), Math.exp(v) < 10 ? 1 : 0)}x class median`,
  fam_n: (v) => num(v),
  fam_spread: (v) => `${num(v, 1)}x`,
};
const HIDDEN_FEATURES = new Set(["ref_type"]); // internal bookkeeping, not a property of the item

/** Readable value for a model feature in "Why this price"; null hides the row. */
export function featureValue(feature: string, value: unknown): string | null {
  if (HIDDEN_FEATURES.has(feature)) return null;
  if (value === null || value === undefined || (typeof value === "number" && !Number.isFinite(value))) return "n/a";
  if (typeof value === "number") return FEATURE_VALUE[feature]?.(value) ?? num(value, Math.abs(value) < 10 ? 2 : 0);
  const v = String(value);
  if (v === "NA" || v === "") return "n/a";
  if (feature === "niin_country") return v === "00" || v === "01" ? "US" : `non-US (${v})`;
  if (feature === "fsc") return FSC_NAMES[v] ? `${v} ${FSC_NAMES[v]}` : v;
  return v;
}

/** +15551810111 -> (555) 181-0111; other formats unchanged. */
export function formatPhone(p: string | null | undefined): string {
  if (!p) return "";
  const m = p.replace(/[^\d+]/g, "").match(/^(?:\+?1)?(\d{3})(\d{3})(\d{4})$/);
  return m ? `(${m[1]}) ${m[2]}-${m[3]}` : p;
}
