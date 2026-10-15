/** Dashboard filtering (pure, unit-tested). */
import type { Schemas } from "@/lib/api/client";

export type DashboardLine = Schemas["DashboardLine"];

export type FlagKey = "fast_award" | "approved_source_only" | "low_data" | "provisional" | "needs_quote";
export type StatusFilter = "all" | "not_started" | "drafts" | "sent" | "quoted" | "needs_review";

export interface DashboardFilters {
  fscs: string[]; // empty = all
  dueWithin: number | null; // days
  flags: FlagKey[]; // lines must have all selected flags
  status: StatusFilter;
  search: string;
}

export const DEFAULT_FILTERS: DashboardFilters = { fscs: [], dueWithin: null, flags: [], status: "all", search: "" };

export function statusOf(l: DashboardLine): Exclude<StatusFilter, "all"> {
  const q = l.quote_status;
  if (q.needs_review > 0) return "needs_review";
  if (q.parsed > 0) return "quoted";
  if (q.sent > 0 || q.replied > 0) return "sent";
  if (q.draft > 0) return "drafts";
  return "not_started";
}

export function filterLines(lines: DashboardLine[], f: DashboardFilters): DashboardLine[] {
  const q = f.search.trim().toLowerCase();
  return lines.filter((l) => {
    if (f.fscs.length && !f.fscs.includes(l.fsc)) return false;
    if (f.dueWithin !== null && (l.days_left === null || l.days_left === undefined || l.days_left > f.dueWithin)) return false;
    for (const flag of f.flags) if (!l.flags[flag]) return false;
    if (f.status !== "all" && statusOf(l) !== f.status) return false;
    if (q) {
      const hay = `${l.solicitation_number} ${l.nsn} ${l.nomenclature ?? ""}`.toLowerCase();
      if (!hay.includes(q.replace(/-/g, ""))) return false;
    }
    return true;
  });
}

export function splitNoBid(lines: DashboardLine[]): { bid: DashboardLine[]; noBid: DashboardLine[] } {
  return { bid: lines.filter((l) => !l.no_bid), noBid: lines.filter((l) => l.no_bid) };
}

export type SortKey = "best" | "expected_profit" | "due" | "line_value" | "p_win";

const lineValue = (l: DashboardLine): number => (l.predicted_median ?? 0) * l.quantity;

export function sortLines(lines: DashboardLine[], key: SortKey): DashboardLine[] {
  if (key === "best") {
    // Lines priced against a real supplier quote first, by expected profit x prediction confidence (rank_score).
    // Then unquoted lines by how much they are worth pursuing (triage_score: profit if sourced x P(sourceable) x
    // P(quote in time) x P(winnable) - pursuit cost). Anything left, biggest order first.
    const tier = (l: DashboardLine) => (l.rank_score != null ? 0 : l.triage_score != null ? 1 : 2);
    return [...lines].sort((a, b) => {
      const ta = tier(a);
      const tb = tier(b);
      if (ta !== tb) return ta - tb;
      if (ta === 0) return (b.rank_score as number) - (a.rank_score as number);
      if (ta === 1) return (b.triage_score as number) - (a.triage_score as number);
      return lineValue(b) - lineValue(a);
    });
  }
  const v = (l: DashboardLine): number => {
    switch (key) {
      case "due":
        return -(l.days_left ?? 9999);
      case "line_value":
        return lineValue(l);
      case "p_win":
        return l.p_win ?? 0;
      default:
        return l.expected_profit ?? 0;
    }
  };
  return [...lines].sort((a, b) => v(b) - v(a));
}
