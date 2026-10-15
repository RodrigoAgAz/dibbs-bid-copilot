import { useId } from "react";
import { cn } from "@/lib/utils";

export const PRODUCT = "The Invisible Hand";
export const TAGLINE = "Bid where the market clears";

/**
 * The mark: an open hand drawn in gold that fades out toward the wrist, the "invisible hand" that sets the
 * price, on an ink tile. Hand outline after Lucide's "hand" (ISC license).
 */
export function HandMark({ className, title = PRODUCT }: { className?: string; title?: string }) {
  const id = useId().replace(/:/g, "");
  return (
    <svg viewBox="0 0 32 32" className={cn("h-7 w-7 shrink-0", className)} role="img" aria-label={title}>
      <defs>
        <linearGradient id={`${id}-tile`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" style={{ stopColor: "hsl(var(--brand-ink-2))" }} />
          <stop offset="1" style={{ stopColor: "hsl(var(--brand-ink))" }} />
        </linearGradient>
        <linearGradient id={`${id}-hand`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" style={{ stopColor: "hsl(var(--brand-gold))" }} />
          <stop offset="0.65" style={{ stopColor: "hsl(var(--brand-gold))", stopOpacity: 0.85 }} />
          <stop offset="1" style={{ stopColor: "hsl(var(--brand-gold))", stopOpacity: 0.15 }} />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="8" fill={`url(#${id}-tile)`} />
      <g
        transform="translate(4.2 3.6) scale(0.98)"
        fill="none"
        stroke={`url(#${id}-hand)`}
        strokeWidth="1.9"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M18 11V6a2 2 0 0 0-2-2a2 2 0 0 0-2 2" />
        <path d="M14 10V4a2 2 0 0 0-2-2a2 2 0 0 0-2 2v2" />
        <path d="M10 10.5V6a2 2 0 0 0-2-2a2 2 0 0 0-2 2v8" />
        <path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15" />
      </g>
    </svg>
  );
}

/** Mark + wordmark. `size="lg"` for auth / landing headers. */
export function Brand({ size = "md", className, sub }: { size?: "sm" | "md" | "lg"; className?: string; sub?: React.ReactNode }) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <HandMark className={size === "lg" ? "h-9 w-9" : size === "sm" ? "h-6 w-6" : "h-7 w-7"} />
      <span className="leading-none">
        <span className={cn("block font-serif tracking-tight text-foreground", size === "lg" ? "text-xl" : size === "sm" ? "text-[15px]" : "text-[17px]")}>
          <span className="text-[0.72em] uppercase tracking-[0.14em] text-muted-foreground">The </span>
          Invisible Hand
        </span>
        {sub ? <span className="mt-1 block text-[11px] text-muted-foreground">{sub}</span> : null}
      </span>
    </span>
  );
}
