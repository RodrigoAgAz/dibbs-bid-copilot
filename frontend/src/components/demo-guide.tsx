"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Compass, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { statusOf, type DashboardLine } from "@/lib/dashboard";
import { useMe } from "@/lib/hooks";

const KEY = "demo-guide-dismissed";

/** A three-stop tour for visitors of the read-only public demo. */
export function DemoGuide({ lines }: { lines: DashboardLine[] }) {
  const { data: me } = useMe();
  const [dismissed, setDismissed] = useState(true);
  useEffect(() => {
    try {
      setDismissed(window.sessionStorage.getItem(KEY) === "1");
    } catch {
      setDismissed(false);
    }
  }, []);
  if (!me || me.user.role !== "viewer" || me.organization.plan !== "demo" || dismissed) return null;

  const real = lines.filter((l) => !l.no_bid && !l.solicitation_number.startsWith("SPEZZ"));
  const quoted = real.find((l) => statusOf(l) === "quoted" || statusOf(l) === "needs_review");
  const story = real.find((l) => l.flags.confidence === "high" && l.recommended_bid != null) ?? real.find((l) => l.recommended_bid != null);
  const quoteFirst = real.filter((l) => l.flags.needs_quote).length;

  function dismiss() {
    setDismissed(true);
    try {
      window.sessionStorage.setItem(KEY, "1");
    } catch {
      /* private mode */
    }
  }

  return (
    <div className="mb-4 rounded-lg border border-primary/30 bg-primary/5 p-4" data-testid="demo-guide">
      <div className="mb-2 flex items-start justify-between gap-3">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <Compass className="h-4 w-4 text-primary" /> A 2-minute tour
        </div>
        <Button variant="ghost" size="xs" onClick={dismiss} aria-label="Hide the tour">
          <X />
        </Button>
      </div>
      <ol className="grid gap-3 text-sm md:grid-cols-3">
        <li>
          <div className="font-medium">1. Today&apos;s best bets</div>
          <p className="text-xs text-muted-foreground">
            Real DIBBS solicitations from the last few days, ranked by expected profit weighted by how well the price is known (the dot next
            to P(win): green = high confidence).
          </p>
        </li>
        <li>
          <div className="font-medium">2. The price story</div>
          <p className="text-xs text-muted-foreground">
            Award history in plain English, who usually wins, and the bid with its chance of winning.{" "}
            {story && (
              <Link className="text-primary hover:underline" href={`/rfq/${story.solicitation_number}`}>
                Open {story.nomenclature ?? story.solicitation_number}
              </Link>
            )}
          </p>
        </li>
        <li>
          <div className="font-medium">3. Supplier quotes to a firm bid</div>
          <p className="text-xs text-muted-foreground">
            RFQ emails out, replies read automatically, landed cost and bid recomputed (demo suppliers are fictional).{" "}
            {quoted && (
              <Link className="text-primary hover:underline" href={`/rfq/${quoted.solicitation_number}`}>
                See a quoted line
              </Link>
            )}
            {quoteFirst > 0 && <> · {quoteFirst} lines have no price history and are marked &quot;Quote first&quot;.</>}
          </p>
        </li>
      </ol>
    </div>
  );
}
