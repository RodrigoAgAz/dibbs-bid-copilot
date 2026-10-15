import type { components } from "@/lib/api/schema";

export type PilotComparison = components["schemas"]["PilotComparison"];
export type PilotOut = components["schemas"]["PilotOut"];

/** Awarded lines needed before the sign of the increment is trusted (dibbs_ml.pilot.REGRET_MIN_LINES). */
export const PILOT_MIN_LINES = 30;

export type Verdict = { label: string; tone: "good" | "warn" | "bad" | "neutral"; sentence: string };

/** What the head-to-head says, in one line, with the right amount of confidence. */
export function pilotVerdict(h: PilotComparison, inputs: Record<string, number> = {}, minLines = PILOT_MIN_LINES): Verdict {
  if (h.n === 0) {
    const bids = inputs.firm_bids ?? 0;
    const awarded = inputs.awarded ?? 0;
    if (bids > 0 && awarded === 0) {
      return { label: "Awaiting awards", tone: "neutral", sentence: `${bids} of your bids are recorded; none of those lines has been awarded yet.` };
    }
    if (bids > 0) {
      return {
        label: "Nothing to compare yet",
        tone: "neutral",
        sentence: `${bids} of your bids are recorded and ${awarded} ${awarded === 1 ? "line is" : "lines are"} awarded, but on none of them had the tool priced the line before the award posted.`,
      };
    }
    return {
      label: "No data yet",
      tone: "neutral",
      sentence: "Record your bids and costs on RFQ pages, or import them (import-bids, import-costs, import-outcomes), and come back once awards post.",
    };
  }
  const lines = `${h.n} awarded ${h.n === 1 ? "line" : "lines"} you bid on`;
  const inc = h.increment;
  const perHour = h.increment_per_firm_hour;
  const money = (v: number) => `${v < 0 ? "−" : ""}$${Math.abs(v).toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
  const amount = perHour == null ? money(inc) : `${money(inc)} (${money(perHour)} per hour you spent)`;
  if (h.n < minLines) {
    return {
      label: "Too early to call",
      tone: "warn",
      sentence: `${lines}: the tool ${inc >= 0 ? "adds" : "loses"} ${amount}, but at least ${minLines} lines are needed before the sign is trusted.`,
    };
  }
  if (inc > 0) return { label: "Tool ahead", tone: "good", sentence: `${lines}: following the tool would have added ${amount}.` };
  if (inc < 0) return { label: "Your process ahead", tone: "bad", sentence: `${lines}: following the tool would have lost ${amount}.` };
  return { label: "Even", tone: "neutral", sentence: `${lines}: the same completed profit either way.` };
}

/** How much of the comparison rests on estimated rather than recorded costs. */
export function costBasisNote(h: PilotComparison): string | null {
  if (h.n === 0 || h.n_estimated_cost === 0) return null;
  if (h.n_real_cost === 0) return "No purchase costs recorded: every profit here uses the landed-cost estimate the bid was priced against.";
  return `${h.n_estimated_cost} of ${h.n} lines have no recorded purchase cost and use the estimated landed cost.`;
}
