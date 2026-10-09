import { describe, expect, it } from "vitest";
import { buildWaterfall, bestPoint, historyPoints, quantile } from "./charts";
import { DEFAULT_FILTERS, filterLines, splitNoBid, statusOf, type DashboardLine } from "./dashboard";
import { countdown, formatNsn, formatSolicitation, isDemoSolicitation, money, pct } from "./format";

describe("format", () => {
  it("formats NSNs and solicitation numbers", () => {
    expect(formatNsn("5305011209897")).toBe("5305-01-120-9897");
    expect(formatNsn("4730LLQJ07784")).toBe("4730-LL-QJ0-7784");
    expect(formatNsn("short")).toBe("short");
    expect(formatSolicitation("SPE4A627T0486")).toBe("SPE4A6-27-T-0486");
    expect(isDemoSolicitation("SPEZZ126T0001")).toBe(true);
    expect(isDemoSolicitation("SPE4A627T0486")).toBe(false);
  });

  it("formats money with sub-dollar precision", () => {
    expect(money(13.8)).toBe("$13.80");
    expect(money(0.488)).toBe("$0.488");
    expect(money(null)).toBe("—");
    expect(money(125000, { compact: true })).toBe("$125K");
    expect(pct(0.4567)).toBe("46%");
  });

  it("labels countdowns by urgency", () => {
    expect(countdown(0)).toEqual({ label: "today", tone: "danger" });
    expect(countdown(2).tone).toBe("warn");
    expect(countdown(9)).toEqual({ label: "9 days", tone: "normal" });
    expect(countdown(-1).label).toBe("closed");
  });
});

describe("charts", () => {
  it("builds a landed-cost waterfall that sums to the total", () => {
    const steps = buildWaterfall({
      supplier_unit_price: 4,
      effective_unit_price: 4,
      freight_per_unit: 0.25,
      packaging_per_unit: 0.1,
      inspection_allowance: 0,
      financing_cost: 0.0329,
      overhead: 0.2,
      landed: 4.5829,
    });
    expect(steps.map((s) => s.name)).toEqual(["Supplier price", "Freight", "Packaging", "Financing", "Overhead", "Landed"]);
    const last = steps[steps.length - 2];
    expect(last.base + last.value).toBeCloseTo(4.5829, 4);
    expect(steps[steps.length - 1]).toMatchObject({ total: true, value: 4.5829 });
    expect(buildWaterfall(null)).toEqual([]);
  });

  it("finds the best curve point and sorts history", () => {
    expect(bestPoint([{ bid: 1, p_win: 0.9, expected_profit: 5 }, { bid: 2, p_win: 0.5, expected_profit: 8 }])?.bid).toBe(2);
    const pts = historyPoints([
      { award_date: "2025-01-02", quantity: 10, unit_price: 2, awardee_cage: "A" },
      { award_date: "2024-01-02", quantity: 0, unit_price: 1, awardee_cage: null },
    ]);
    expect(pts[0].date).toBe("2024-01-02");
    expect(pts[0].z).toBe(1);
    expect(quantile([0.05, 0.5, 0.95], [1, 2, 3], 0.5)).toBe(2);
  });
});

function line(over: Partial<DashboardLine>): DashboardLine {
  return {
    line_id: 1,
    solicitation_number: "SPE4A627T0486",
    line_no: 1,
    type_code: "T",
    return_by_date: "2026-10-13",
    days_left: 4,
    nsn: "5305011209897",
    fsc: "5305",
    nomenclature: "SCREW,MACHINE",
    quantity: 10,
    unit_of_issue: "EA",
    set_aside: "NONE",
    predicted_median: 10,
    landed_cost: 7,
    recommended_bid: 9,
    conservative_bid: 8,
    aggressive_bid: 11,
    p_win: 0.5,
    expected_profit: 10,
    no_bid: false,
    no_bid_reason: null,
    actual_bid: null,
    quote_status: { candidates: 0, draft: 0, sent: 0, replied: 0, parsed: 0, declined: 0, no_response: 0, failed: 0, needs_review: 0, label: "" },
    flags: { fast_award: false, approved_source_only: false, low_data: false, cold_start: false, provisional: true, first_article: false, aidc: false },
    ...over,
  } as DashboardLine;
}

describe("dashboard filters", () => {
  const lines = [
    line({ line_id: 1 }),
    line({ line_id: 2, fsc: "5310", days_left: 10, nomenclature: "NUT,PLAIN" }),
    line({ line_id: 3, no_bid: true, flags: { ...line({}).flags, approved_source_only: true } }),
    line({ line_id: 4, quote_status: { ...line({}).quote_status, parsed: 1, needs_review: 1 } }),
  ];

  it("filters by FSC, due date, flags, status and search", () => {
    expect(filterLines(lines, { ...DEFAULT_FILTERS, fscs: ["5310"] }).map((l) => l.line_id)).toEqual([2]);
    expect(filterLines(lines, { ...DEFAULT_FILTERS, dueWithin: 7 }).map((l) => l.line_id)).toEqual([1, 3, 4]);
    expect(filterLines(lines, { ...DEFAULT_FILTERS, flags: ["approved_source_only"] }).map((l) => l.line_id)).toEqual([3]);
    expect(filterLines(lines, { ...DEFAULT_FILTERS, status: "needs_review" }).map((l) => l.line_id)).toEqual([4]);
    expect(filterLines(lines, { ...DEFAULT_FILTERS, search: "nut" }).map((l) => l.line_id)).toEqual([2]);
    expect(filterLines(lines, { ...DEFAULT_FILTERS, search: "5305-01-120" }).length).toBe(4);
  });

  it("separates no-bid lines and derives status", () => {
    const { bid, noBid } = splitNoBid(lines);
    expect(noBid.map((l) => l.line_id)).toEqual([3]);
    expect(bid.length).toBe(3);
    expect(statusOf(lines[0])).toBe("not_started");
    expect(statusOf(lines[3])).toBe("needs_review");
  });
});
