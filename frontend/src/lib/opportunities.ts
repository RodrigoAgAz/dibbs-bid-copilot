/** Home-page work lanes and plain-language reasons for each opportunity (pure, unit-tested). */
import { statusOf, type DashboardFilters, type DashboardLine } from "./dashboard";
import { money, plural } from "./format";

export type LaneKey = "closing" | "ready" | "quote" | "review" | "new";

export interface Lane {
  key: LaneKey;
  title: string;
  blurb: string; // what the lane is, in one line
  action: string; // what to do with it
  /** The same lane as a deep link into the full list (/lines?...). */
  query: Partial<Record<"status" | "flag" | "due", string>>;
}

export const LANES: Lane[] = [
  {
    key: "closing",
    title: "Closing soon",
    blurb: "Worth bidding and due within 3 days",
    action: "Check the price and bid before the deadline",
    query: { due: "3" },
  },
  {
    key: "ready",
    title: "Ready to bid",
    blurb: "A supplier has quoted, so the bid is priced against your real cost",
    action: "Review and submit on DIBBS",
    query: { status: "quoted" },
  },
  {
    key: "quote",
    title: "Worth quoting",
    blurb: "The winning price is well known; you still need a supplier cost",
    action: "Request supplier quotes",
    query: { status: "not_started" },
  },
  {
    key: "review",
    title: "Replies to review",
    blurb: "Supplier replies the parser wasn't sure about",
    action: "Approve or correct the quote",
    query: { status: "needs_review" },
  },
  {
    key: "new",
    title: "New parts",
    blurb: "No usable award history, so there's no price to predict",
    action: "Get a supplier quote first",
    query: { flag: "needs_quote" },
  },
];

const biddable = (l: DashboardLine) => !l.no_bid && l.recommended_bid != null;
const due = (l: DashboardLine) => l.days_left ?? 9999;
const rank = (l: DashboardLine) => l.rank_score ?? 0;
// Unquoted lines: worth pursuing = profit if sourced x P(sourceable) x P(quote in time) x P(winnable) - effort.
const triage = (l: DashboardLine) => l.triage_score ?? -Infinity;

/** The lines in a lane, most pressing first. */
export function laneLines(lines: DashboardLine[], lane: LaneKey): DashboardLine[] {
  switch (lane) {
    case "closing":
      return lines.filter((l) => biddable(l) && due(l) <= 3).sort((a, b) => due(a) - due(b) || rank(b) - rank(a));
    case "ready":
      return lines.filter((l) => biddable(l) && !l.flags.provisional).sort((a, b) => due(a) - due(b) || rank(b) - rank(a));
    case "quote":
      return lines
        .filter((l) => biddable(l) && l.flags.provisional && statusOf(l) === "not_started" && ["high", "medium"].includes(l.flags.confidence ?? ""))
        .sort((a, b) => triage(b) - triage(a) || orderValue(b) - orderValue(a));
    case "review":
      return lines.filter((l) => !l.no_bid && statusOf(l) === "needs_review").sort((a, b) => due(a) - due(b));
    case "new":
      return lines.filter((l) => !l.no_bid && l.flags.needs_quote).sort((a, b) => due(a) - due(b) || orderValue(b) - orderValue(a));
  }
}

/** The lane to open first: the most urgent one that has anything in it. */
export function defaultLane(counts: Record<LaneKey, number>): LaneKey {
  return (["closing", "review", "ready", "quote", "new"] as LaneKey[]).find((k) => counts[k] > 0) ?? "quote";
}

/** Size of the order at the recommended bid (or the predicted price when there's no bid yet). */
export function orderValue(l: DashboardLine): number {
  return (l.recommended_bid ?? l.predicted_median ?? 0) * l.quantity;
}

const CONFIDENCE_TEXT: Record<string, string> = {
  high: "Well-established price: several past awards that agree with each other",
  medium: "Some price history, with a moderate spread",
  low: "Thin or scattered price history: check it before bidding",
  none: "No usable award history for this part",
};

/** One-sentence verdict for a line. */
export function verdict(l: DashboardLine): string {
  if (l.flags.needs_quote || l.recommended_bid == null) {
    return "No price to predict yet: a supplier quote sets the cost and the bid.";
  }
  const chance = l.p_win == null ? "" : `, which wins about ${Math.round(l.p_win * 10)} times in 10`;
  return `Bid ${money(l.recommended_bid)} per ${l.unit_of_issue ?? "unit"}${chance}.`;
}

/** Up to `max` short reasons, most decision-relevant first. */
export function reasons(l: DashboardLine, max = 3): string[] {
  const out: string[] = [];
  const s = statusOf(l);
  if (s === "needs_review") out.push("A supplier reply needs your review before it counts");
  else if (s === "quoted" && !l.flags.provisional && l.landed_cost != null)
    out.push(`Priced against a supplier quote (landed cost ${money(l.landed_cost)})`);
  else if (s === "sent") out.push("Quote requests are out; waiting on supplier replies");
  else if (!l.flags.needs_quote) out.push("No supplier quote yet: the bid assumes a typical cost");
  out.push(CONFIDENCE_TEXT[l.flags.confidence ?? "low"] ?? CONFIDENCE_TEXT.low);
  if (l.flags.approved_source_only) out.push("Approved sources only: the quote must be for an approved part number");
  if (l.flags.first_article) out.push("First article testing required (extra cost and time)");
  return out.slice(0, max);
}

/** "Due today", "Due tomorrow", "Due in 5 days", "Past due". */
export function dueText(days: number | null | undefined): string {
  if (days == null) return "No due date";
  if (days < 0) return "Past due";
  if (days === 0) return "Due today";
  if (days === 1) return "Due tomorrow";
  return `Due in ${plural(days, "day")}`;
}

/** Filters for the full list from a deep link (/lines?status=quoted&flag=needs_quote&due=3). */
export function filtersFromQuery(q: URLSearchParams, base: DashboardFilters): DashboardFilters {
  const f = { ...base };
  const status = q.get("status");
  if (status && ["not_started", "drafts", "sent", "quoted", "needs_review"].includes(status)) f.status = status as DashboardFilters["status"];
  const flag = q.get("flag");
  if (flag && ["fast_award", "approved_source_only", "low_data", "provisional", "needs_quote"].includes(flag))
    f.flags = [flag as DashboardFilters["flags"][number]];
  const d = Number(q.get("due"));
  if (Number.isFinite(d) && d > 0) f.dueWithin = d;
  const search = q.get("q");
  if (search) f.search = search;
  const fsc = q.get("fsc");
  if (fsc && /^\d{4}$/.test(fsc)) f.fscs = [fsc];
  return f;
}
