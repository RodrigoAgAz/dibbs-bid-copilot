/** Dashboard lines as CSV for Excel (pure, unit-tested). */
import type { DashboardLine } from "./dashboard";

const COLUMNS: [string, (l: DashboardLine, origin: string) => unknown][] = [
  ["Return by", (l) => l.return_by_date],
  ["Solicitation", (l) => l.solicitation_number],
  ["Line", (l) => l.line_no],
  ["NSN", (l) => l.nsn],
  ["FSC", (l) => l.fsc],
  ["Item", (l) => l.nomenclature],
  ["Qty", (l) => l.quantity],
  ["UI", (l) => l.unit_of_issue],
  ["Set-aside", (l) => l.set_aside],
  ["Predicted median", (l) => l.predicted_median],
  ["Landed cost", (l) => l.landed_cost],
  ["Recommended bid", (l) => l.recommended_bid],
  ["P(win)", (l) => (l.p_win == null ? null : Math.round(l.p_win * 1000) / 1000)],
  ["Expected profit", (l) => (l.expected_profit == null ? null : Math.round(l.expected_profit * 100) / 100)],
  ["Confidence", (l) => l.flags.confidence],
  ["Quote first", (l) => (l.flags.needs_quote ? "yes" : "")],
  ["No bid", (l) => (l.no_bid ? l.no_bid_reason ?? "yes" : "")],
  ["Quotes", (l) => l.quote_status.label],
  ["Link", (l, origin) => `${origin}/rfq/${l.solicitation_number}`],
];

function cell(v: unknown): string {
  if (v === null || v === undefined) return "";
  const s = String(v);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function linesToCsv(lines: DashboardLine[], origin = ""): string {
  const head = COLUMNS.map(([h]) => h).join(",");
  const rows = lines.map((l) => COLUMNS.map(([, f]) => cell(f(l, origin))).join(","));
  return [head, ...rows].join("\r\n") + "\r\n";
}

export function downloadCsv(filename: string, csv: string): void {
  const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" }); // BOM: Excel reads UTF-8
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
