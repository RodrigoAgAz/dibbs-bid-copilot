import { describe, expect, it } from "vitest";
import { buildWaterfall, bestPoint, historyPoints, niceTicks, quantile } from "./charts";
import { DEFAULT_FILTERS, filterLines, splitNoBid, statusOf, type DashboardLine } from "./dashboard";
import { countdown, featureValue, formatNsn, formatPhone, formatSolicitation, isDemoSolicitation, modelLabel, money, pct, plural, timeAgo } from "./format";

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

  it("formats relative times", () => {
    const now = new Date("2026-10-14T15:00:00Z");
    expect(timeAgo(null, now)).toBe("never");
    expect(timeAgo("2026-10-14T14:59:40Z", now)).toBe("just now");
    expect(timeAgo("2026-10-14T14:48:00Z", now)).toBe("12 min ago");
    expect(timeAgo("2026-10-14T12:00:00Z", now)).toBe("3 h ago");
    expect(timeAgo("2026-10-11T15:00:00Z", now)).toBe("3 days ago");
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
    expect(steps.map((s) => s.name)).toEqual(["Supplier", "Freight", "Packaging", "Financing", "Overhead", "Landed"]);
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

describe("dashboard sorting", () => {
  it("sorts by due date, value and expected profit", async () => {
    const { sortLines } = await import("./dashboard");
    const ls = [
      line({ line_id: 1, days_left: 5, expected_profit: 10, predicted_median: 1, quantity: 10 }),
      line({ line_id: 2, days_left: 1, expected_profit: 5, predicted_median: 100, quantity: 10 }),
      line({ line_id: 3, days_left: 9, expected_profit: 50, predicted_median: 2, quantity: 10 }),
    ];
    expect(sortLines(ls, "expected_profit").map((l) => l.line_id)).toEqual([3, 1, 2]);
    expect(sortLines(ls, "due").map((l) => l.line_id)).toEqual([2, 1, 3]);
    expect(sortLines(ls, "line_value").map((l) => l.line_id)).toEqual([2, 3, 1]);
  });

  it("ranks best bets by confidence-weighted profit, unpriced lines last by value", async () => {
    const { sortLines } = await import("./dashboard");
    const ls = [
      line({ line_id: 1, rank_score: 30, predicted_median: 1, quantity: 10 }),
      line({ line_id: 2, rank_score: null, predicted_median: 5, quantity: 10 }),
      line({ line_id: 3, rank_score: 90, predicted_median: 1, quantity: 10 }),
      line({ line_id: 4, rank_score: null, predicted_median: 50, quantity: 10 }),
    ];
    expect(sortLines(ls, "best").map((l) => l.line_id)).toEqual([3, 1, 4, 2]);
  });
});

describe("csv export", () => {
  it("quotes awkward cells and keeps one row per line", async () => {
    const { linesToCsv } = await import("./csv");
    const csv = linesToCsv(
      [line({ line_id: 1, solicitation_number: "SPE4A627T0486", nomenclature: 'SCREW, CAP, 1/4" HEX', recommended_bid: 0.44, p_win: 0.6234 })],
      "https://app.example.com",
    );
    const [head, row] = csv.trim().split("\r\n");
    expect(head.startsWith("Return by,Solicitation,Line,NSN")).toBe(true);
    expect(row).toContain('"SCREW, CAP, 1/4"" HEX"');
    expect(row).toContain(",0.44,0.623,");
    expect(row.endsWith("https://app.example.com/rfq/SPE4A627T0486")).toBe(true);
  });
});

describe("readable model output", () => {
  it("shows feature values in broker terms and hides internals", () => {
    expect(featureValue("log_quantity", Math.log1p(228))).toBe("228");
    expect(featureValue("n_approved_sources", 0)).toBe("0");
    expect(featureValue("days_since_last_award", 1)).toBe("1 day");
    expect(featureValue("inflation_change", Math.log(1.04))).toBe("+4%");
    expect(featureValue("f_min", Math.log(0.8))).toBe("−20% vs last");
    expect(featureValue("first_article", 1)).toBe("yes");
    expect(featureValue("niin_country", "01")).toBe("US");
    expect(featureValue("niin_country", "12")).toBe("non-US (12)");
    expect(featureValue("fsc", "5330")).toBe("5330 Packing & gaskets");
    expect(featureValue("unit_of_issue", "NA")).toBe("n/a");
    expect(featureValue("ref_type", "last_award")).toBeNull();
    expect(featureValue("volatility", null)).toBe("n/a");
    expect(modelLabel("lgbm_quantile_v2")).toBe("pricing model v2");
  });

  it("pluralizes and formats phones", () => {
    expect(plural(1, "past award")).toBe("1 past award");
    expect(plural(3, "past award")).toBe("3 past awards");
    expect(plural(1204, "line")).toBe("1,204 lines");
    expect(formatPhone("+15551810111")).toBe("(555) 181-0111");
    expect(formatPhone("555-181-0111")).toBe("(555) 181-0111");
    expect(formatPhone("+44 20 7946 0958")).toBe("+44 20 7946 0958");
  });

  it("picks round axis ticks that cover the data", () => {
    expect(niceTicks(880, 1163)).toEqual([800, 900, 1000, 1100, 1200]);
    expect(niceTicks(0.0482, 0.306)).toEqual([0, 0.1, 0.2, 0.3, 0.4]);
    const t = niceTicks(645.65, 1498.93);
    expect(t[0]).toBeLessThanOrEqual(645.65);
    expect(t[t.length - 1]).toBeGreaterThanOrEqual(1498.93);
    expect(t.every((v) => Number.isInteger(v / 100) || Number.isInteger(v / 250))).toBe(true);
    expect(niceTicks(5, 5).length).toBeGreaterThan(1);
  });

  it("marks derived award prices in the history points", () => {
    const pts = historyPoints([
      { award_date: "2026-01-01", unit_price: 2, quantity: 5, awardee_cage: "1ABC2" },
      { award_date: "2026-02-01", unit_price: 900, quantity: 5, awardee_cage: "1ABC2", verified: false },
    ]);
    expect(pts.map((p) => p.verified)).toEqual([true, false]);
  });
});
