import { describe, expect, it } from "vitest";
import { DEFAULT_FILTERS, type DashboardLine } from "./dashboard";
import { defaultLane, dueText, filtersFromQuery, laneLines, orderValue, reasons, verdict } from "./opportunities";

let id = 0;
type Over = Partial<Omit<DashboardLine, "flags" | "quote_status">> & { flags?: Partial<DashboardLine["flags"]>; quote?: Partial<DashboardLine["quote_status"]> };
function line(over: Over = {}): DashboardLine {
  const { flags, quote, ...rest } = over;
  return {
    line_id: ++id,
    solicitation_number: `SPE7M026T${id}`,
    line_no: 1,
    type_code: "T",
    return_by_date: "2026-10-20",
    days_left: 5,
    nsn: "5305011209897",
    fsc: "5305",
    nomenclature: "SCREW",
    quantity: 100,
    unit_of_issue: "EA",
    set_aside: null,
    predicted_median: 2,
    landed_cost: 1.4,
    recommended_bid: 1.9,
    conservative_bid: 1.8,
    aggressive_bid: 2.1,
    p_win: 0.62,
    expected_profit: 30,
    rank_score: 30,
    no_bid: false,
    no_bid_reason: null,
    actual_bid: null,
    quote_status: { candidates: 3, draft: 0, sent: 0, replied: 0, parsed: 0, declined: 0, no_response: 0, needs_review: 0, label: "", ...quote },
    flags: { fast_award: false, approved_source_only: false, low_data: false, cold_start: false, provisional: true, first_article: false, aidc: false, needs_quote: false, confidence: "high", ...flags },
    ...rest,
  } as DashboardLine;
}

describe("home lanes", () => {
  it("sorts lines into lanes", () => {
    const soon = line({ days_left: 1 });
    const later = line({ days_left: 9 });
    const quoted = line({ flags: { provisional: false }, quote: { parsed: 1 } });
    const review = line({ quote: { needs_review: 1 } });
    const fresh = line({ recommended_bid: null, p_win: null, flags: { needs_quote: true, cold_start: true, confidence: "none" } });
    const noBid = line({ days_left: 0, no_bid: true });
    const all = [soon, later, quoted, review, fresh, noBid];
    expect(laneLines(all, "closing")).toEqual([soon]);
    expect(laneLines(all, "ready")).toEqual([quoted]);
    expect(laneLines(all, "quote").map((l) => l.line_id)).toEqual(expect.arrayContaining([soon.line_id, later.line_id]));
    expect(laneLines(all, "quote")).not.toContain(review);
    expect(laneLines(all, "review")).toEqual([review]);
    expect(laneLines(all, "new")).toEqual([fresh]);
    expect(defaultLane({ closing: 1, ready: 0, quote: 3, review: 1, new: 0 })).toBe("closing");
    expect(defaultLane({ closing: 0, ready: 0, quote: 3, review: 1, new: 0 })).toBe("review");
    expect(defaultLane({ closing: 0, ready: 0, quote: 0, review: 0, new: 0 })).toBe("quote");
  });

  it("explains a line in plain language", () => {
    const l = line({ flags: { approved_source_only: true } });
    expect(verdict(l)).toBe("Bid $1.90 per EA, which wins about 6 times in 10.");
    expect(reasons(l)[0]).toContain("No supplier quote yet");
    expect(reasons(l)).toContain("Approved sources only: the quote must be for an approved part number");
    expect(verdict(line({ recommended_bid: null, flags: { needs_quote: true } }))).toContain("supplier quote");
    expect(reasons(line({ flags: { provisional: false }, quote: { parsed: 1 } }))[0]).toContain("landed cost $1.40");
    expect(orderValue(l)).toBeCloseTo(190);
    expect(dueText(0)).toBe("Due today");
    expect(dueText(1)).toBe("Due tomorrow");
    expect(dueText(4)).toBe("Due in 4 days");
  });

  it("reads list filters from a deep link", () => {
    const f = filtersFromQuery(new URLSearchParams("status=quoted&flag=needs_quote&due=3&q=gasket"), DEFAULT_FILTERS);
    expect(f).toMatchObject({ status: "quoted", flags: ["needs_quote"], dueWithin: 3, search: "gasket" });
    expect(filtersFromQuery(new URLSearchParams("status=bogus&due=x"), DEFAULT_FILTERS)).toEqual(DEFAULT_FILTERS);
  });
});
