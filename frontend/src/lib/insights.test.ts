import { describe, expect, it } from "vitest";
import { competitors, priceStory, type Award } from "./insights";

const a = (award_date: string, unit_price: number, quantity: number, awardee_cage: string | null, awardee_name?: string): Award => ({
  award_date,
  unit_price,
  quantity,
  awardee_cage,
  awardee_name,
});

describe("insights", () => {
  const history = [
    a("2023-03-01", 0.4, 1000, "1ABC2", "ACME FASTENERS"),
    a("2024-02-01", 0.42, 900, "1ABC2", "ACME FASTENERS"),
    a("2025-01-15", 0.45, 1100, "3XYZ9"),
    a("2026-01-10", 0.55, 1000, "1ABC2", "ACME FASTENERS"),
    a("2026-08-20", 0.56, 1200, "1ABC2", "ACME FASTENERS"),
  ];

  it("ranks competitors by wins", () => {
    const c = competitors(history);
    expect(c[0]).toMatchObject({ cage: "1ABC2", name: "ACME FASTENERS", wins: 4, lastPrice: 0.56, lastDate: "2026-08-20" });
    expect(c[1]).toMatchObject({ cage: "3XYZ9", wins: 1, name: null });
  });

  it("tells the price story in plain English", () => {
    const s = priceStory(history, 12000, new Date("2026-10-14T12:00:00Z"));
    expect(s[0]).toMatch(/^5 past awards since 2023\. The last was \$0\.56 each for 1,200 on Aug 20, 2026 to ACME FASTENERS\.$/);
    expect(s.some((x) => x.includes("ranged $0.40–$0.56"))).toBe(true);
    expect(s.some((x) => x.includes("12x the usual quantity"))).toBe(true);
    expect(s.some((x) => /up \d+% from earlier awards/.test(x))).toBe(true);
    expect(s.some((x) => x.startsWith("ACME FASTENERS won 4 of 5 awards"))).toBe(true);
  });

  it("says so when there is no history", () => {
    expect(priceStory([], 10)[0]).toContain("No past awards on file");
  });
});
