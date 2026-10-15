"use client";

import * as React from "react";
import { AlertTriangle, Inbox } from "lucide-react";
import { Badge, Spinner, Tip } from "@/components/ui/misc";
import { useReadOnly } from "@/lib/hooks";
import { cn } from "@/lib/utils";

/** Disables every control inside for read-only seats and the public demo (the API enforces this too). */
export function WriteGuard({ children, className }: { children: React.ReactNode; className?: string }) {
  const readOnly = useReadOnly();
  return (
    <fieldset disabled={readOnly} className={cn("m-0 min-w-0 border-0 p-0", className)} title={readOnly ? "Read-only access" : undefined}>
      {children}
    </fieldset>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
        {description ? <p className="mt-0.5 text-xs text-muted-foreground">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function Stat({ label, value, hint, tone }: { label: string; value: React.ReactNode; hint?: React.ReactNode; tone?: "good" | "warn" | "bad" }) {
  return (
    <div className="rounded-lg border bg-card px-3 py-2.5 shadow-sm">
      <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</div>
      <div
        className={cn(
          "num mt-0.5 text-lg font-semibold",
          tone === "good" && "text-success",
          tone === "warn" && "text-warning",
          tone === "bad" && "text-destructive",
        )}
      >
        {value}
      </div>
      {hint ? <div className="text-[11px] text-muted-foreground">{hint}</div> : null}
    </div>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-10 text-sm text-muted-foreground">
      <Spinner /> {label}
    </div>
  );
}

export function ErrorState({ error }: { error: unknown }) {
  const msg = error instanceof Error ? error.message : String(error ?? "Something went wrong");
  return (
    <div role="alert" className="flex items-center gap-2 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
      <AlertTriangle className="h-4 w-4" /> {msg}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1.5 rounded-lg border border-dashed py-10 text-center">
      <Inbox className="h-6 w-6 text-muted-foreground" />
      <div className="text-sm font-medium">{title}</div>
      {children ? <div className="max-w-md text-xs text-muted-foreground">{children}</div> : null}
    </div>
  );
}

export type LineFlags = {
  fast_award?: boolean;
  approved_source_only?: boolean;
  low_data?: boolean;
  cold_start?: boolean;
  provisional?: boolean;
  first_article?: boolean;
  aidc?: boolean;
  needs_quote?: boolean;
  confidence?: string;
};

export function FlagBadges({ flags, compact, empty = <span className="text-muted-foreground">—</span> }: { flags: LineFlags; compact?: boolean; empty?: React.ReactNode }) {
  const items: { key: string; label: string; short: string; tip: string; variant: "default" | "secondary" | "warning" | "destructive" | "outline" }[] = [];
  if (flags.fast_award)
    items.push({ key: "fa", label: "Fast award", short: "FA", tip: "Fast Award candidate: micro-purchase quotes may be awarded before the return date.", variant: "default" });
  if (flags.approved_source_only)
    items.push({ key: "aso", label: "Approved source only", short: "ASO", tip: "Only quotes for an approved part number count toward the cost floor.", variant: "destructive" });
  // "Quote first" already says the NSN has no history: don't also show "No history".
  if (flags.low_data && !(flags.cold_start && flags.needs_quote))
    items.push({
      key: "ld",
      label: flags.cold_start ? "No history" : "Low data",
      short: flags.cold_start ? "NEW" : "LOW",
      tip: flags.cold_start ? "No past awards for this NSN: FSC-level model with a wider interval." : "Fewer than 3 past awards: wider uncertainty.",
      variant: "warning",
    });
  if (flags.needs_quote)
    items.push({
      key: "nq",
      label: "Quote first",
      short: "QUOTE",
      tip: "No award history for this NSN and no supplier quote yet: request quotes to get a recommended bid.",
      variant: "warning",
    });
  if (flags.provisional && !flags.needs_quote)
    items.push({ key: "pv", label: "Provisional", short: "PROV", tip: "No supplier quote yet: landed cost is estimated from the predicted price.", variant: "secondary" });
  if (flags.first_article) items.push({ key: "fat", label: "First article", short: "FAT", tip: "First article testing required.", variant: "outline" });
  if (flags.aidc) items.push({ key: "aidc", label: "AIDC", short: "AIDC", tip: "Automated Indefinite Delivery Contract.", variant: "outline" });
  if (!items.length) return <>{empty}</>;
  return (
    <span className="inline-flex flex-wrap gap-1">
      {items.map((i) => (
        <Tip key={i.key} content={i.tip}>
          <span>
            <Badge variant={i.variant}>{compact ? i.short : i.label}</Badge>
          </span>
        </Tip>
      ))}
    </span>
  );
}
