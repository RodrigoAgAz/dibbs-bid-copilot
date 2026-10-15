import { describe, expect, it } from "vitest";
import { costBasisNote, pilotVerdict, type PilotComparison } from "./pilot";

function cmp(over: Partial<PilotComparison> = {}): PilotComparison {
  const policy = { bids: 0, no_bids: 0, wins: 0, win_rate: null, profit: 0, hours: 0, profit_per_hour: null, regret: 0 };
  return {
    n: 0,
    n_real_cost: 0,
    n_estimated_cost: 0,
    n_outcome_inferred: 0,
    oracle_profit: 0,
    firm: { ...policy },
    tool: { ...policy },
    increment: 0,
    increment_per_firm_hour: null,
    profit_per_hour_delta: null,
    ...over,
  };
}

describe("pilot verdict", () => {
  it("asks for data when nothing is compared", () => {
    const v = pilotVerdict(cmp());
    expect(v.label).toBe("No data yet");
    expect(v.tone).toBe("neutral");
    expect(v.sentence).toContain("import-bids");
    expect(pilotVerdict(cmp(), { firm_bids: 7, awarded: 0 })).toEqual({
      label: "Awaiting awards",
      tone: "neutral",
      sentence: "7 of your bids are recorded; none of those lines has been awarded yet.",
    });
    const v2 = pilotVerdict(cmp(), { firm_bids: 7, awarded: 1 });
    expect(v2.label).toBe("Nothing to compare yet");
    expect(v2.sentence).toBe("7 of your bids are recorded and 1 line is awarded, but on none of them had the tool priced the line before the award posted.");
  });

  it("refuses to call a small sample either way", () => {
    const v = pilotVerdict(cmp({ n: 12, increment: 1234.5, increment_per_firm_hour: 311.7 }));
    expect(v.label).toBe("Too early to call");
    expect(v.tone).toBe("warn");
    expect(v.sentence).toBe("12 awarded lines you bid on: the tool adds $1,235 ($312 per hour you spent), but at least 30 lines are needed before the sign is trusted.");
    expect(pilotVerdict(cmp({ n: 1, increment: -40 })).sentence).toContain("1 awarded line you bid on: the tool loses −$40,");
  });

  it("calls the sign once there are enough lines", () => {
    expect(pilotVerdict(cmp({ n: 30, increment: 500, increment_per_firm_hour: 50.5 }))).toEqual({
      label: "Tool ahead",
      tone: "good",
      sentence: "30 awarded lines you bid on: following the tool would have added $500 ($51 per hour you spent).",
    });
    const v = pilotVerdict(cmp({ n: 45, increment: -900.4, increment_per_firm_hour: -60.6 }));
    expect(v.label).toBe("Your process ahead");
    expect(v.tone).toBe("bad");
    expect(v.sentence).toContain("would have lost −$900 (−$61 per hour you spent)");
    expect(pilotVerdict(cmp({ n: 40, increment: 0 })).label).toBe("Even");
  });

  it("notes estimated costs", () => {
    expect(costBasisNote(cmp())).toBeNull();
    expect(costBasisNote(cmp({ n: 5, n_real_cost: 5 }))).toBeNull();
    expect(costBasisNote(cmp({ n: 5, n_real_cost: 0, n_estimated_cost: 5 }))).toContain("No purchase costs recorded");
    expect(costBasisNote(cmp({ n: 5, n_real_cost: 3, n_estimated_cost: 2 }))).toBe("2 of 5 lines have no recorded purchase cost and use the estimated landed cost.");
  });
});
