/** Plain-language price story and competitor summary from an NSN's award history (pure, unit-tested). */
import { formatDate, money, num } from "./format";

export interface Award {
  award_date: string;
  unit_price: number;
  quantity: number;
  awardee_cage: string | null;
  awardee_name?: string | null;
}

export interface Competitor {
  cage: string;
  name: string | null;
  wins: number;
  lastDate: string;
  lastPrice: number;
  medianPrice: number;
}

const median = (xs: number[]): number => {
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};

const byDate = (a: Award, b: Award) => a.award_date.localeCompare(b.award_date);

/** Who wins this NSN: awardees by number of wins, most recent first on ties. */
export function competitors(awards: Award[], max = 5): Competitor[] {
  const groups = new Map<string, Award[]>();
  for (const a of awards) {
    const key = a.awardee_cage || "unknown";
    groups.set(key, [...(groups.get(key) ?? []), a]);
  }
  return [...groups.entries()]
    .map(([cage, list]) => {
      const sorted = [...list].sort(byDate);
      const last = sorted[sorted.length - 1];
      return {
        cage,
        name:
          sorted
            .map((a) => a.awardee_name)
            .filter(Boolean)
            .pop() ?? null,
        wins: list.length,
        lastDate: last.award_date,
        lastPrice: last.unit_price,
        medianPrice: median(list.map((a) => a.unit_price)),
      };
    })
    .sort((a, b) => b.wins - a.wins || b.lastDate.localeCompare(a.lastDate))
    .slice(0, max);
}

/** A few plain-English sentences a broker can read in ten seconds. */
export function priceStory(awards: Award[], quantity: number, today: Date = new Date()): string[] {
  if (!awards.length) {
    return [
      "No past awards on file for this NSN, so the price is estimated from similar items in the same supply class.",
      "Treat the range as a rough guide and get a supplier quote before bidding.",
    ];
  }
  const sorted = [...awards].sort(byDate);
  const last = sorted[sorted.length - 1];
  const first = sorted[0];
  const out: string[] = [];
  const who = last.awardee_name || (last.awardee_cage ? `CAGE ${last.awardee_cage}` : null);
  out.push(
    `${sorted.length} past award${sorted.length === 1 ? "" : "s"} since ${first.award_date.slice(0, 4)}. ` +
      `The last was ${money(last.unit_price)} each for ${num(last.quantity)} on ${formatDate(last.award_date)}${who ? ` to ${who}` : ""}.`,
  );
  const recent = sorted.slice(-5).map((a) => a.unit_price);
  if (recent.length >= 3) {
    out.push(`The last ${recent.length} awards ranged ${money(Math.min(...recent))}–${money(Math.max(...recent))} per unit.`);
  }
  const usualQty = median(sorted.map((a) => a.quantity));
  if (usualQty > 0 && quantity > 0) {
    const ratio = quantity / usualQty;
    if (ratio >= 3) {
      out.push(
        `This order (${num(quantity)}) is ${num(ratio, ratio < 10 ? 1 : 0)}x the usual quantity (${num(usualQty)}): larger buys usually win at a lower unit price.`,
      );
    } else if (ratio <= 1 / 3) {
      out.push(`This order (${num(quantity)}) is much smaller than usual (${num(usualQty)}): small buys usually cost more per unit.`);
    }
  }
  const yearAgo = new Date(today);
  yearAgo.setFullYear(yearAgo.getFullYear() - 1);
  const cut = yearAgo.toISOString().slice(0, 10);
  const newer = sorted.filter((a) => a.award_date >= cut).map((a) => a.unit_price);
  const older = sorted.filter((a) => a.award_date < cut).map((a) => a.unit_price);
  if (newer.length >= 2 && older.length >= 2) {
    const change = median(newer) / median(older) - 1;
    if (Math.abs(change) >= 0.1) {
      out.push(
        `Prices in the last 12 months are ${change > 0 ? "up" : "down"} ${Math.round(Math.abs(change) * 100)}% from earlier awards.`,
      );
    }
  }
  // A streak says more about the next award than an all-time count.
  let streak = 1;
  for (let i = sorted.length - 2; i >= 0 && sorted[i].awardee_cage && sorted[i].awardee_cage === last.awardee_cage; i--) streak++;
  if (streak >= 3 && last.awardee_cage) {
    const prices = sorted.slice(-streak).map((a) => a.unit_price);
    out.push(
      `${who} won the last ${streak} awards in a row (${money(Math.min(...prices))}–${money(Math.max(...prices))}): the price to beat is theirs.`,
    );
    return out;
  }
  const top = competitors(sorted, 1)[0];
  if (top && sorted.length >= 3 && top.wins / sorted.length >= 0.5 && top.cage !== "unknown") {
    out.push(`${top.name || `CAGE ${top.cage}`} won ${top.wins} of ${sorted.length} awards: expect them to bid again.`);
  }
  return out;
}
