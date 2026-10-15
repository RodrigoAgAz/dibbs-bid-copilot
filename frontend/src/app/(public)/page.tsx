"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BadgeDollarSign, BarChart3, FileSearch, Inbox, Lock, MailCheck, ShieldCheck, Target } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api, errorMessage, unwrap } from "@/lib/api/client";
import { formatDate, formatSolicitation, money, num, pct } from "@/lib/format";
import { useAuthOptions, useDataHealth } from "@/lib/hooks";

const STEPS = [
  {
    icon: Inbox,
    title: "Every new RFQ in your FSCs",
    body: "Each DIBBS posting is pulled in as it lands, filtered to the federal supply classes you sell, with buyer, set-aside, FOB, inspection and approved-source details.",
  },
  {
    icon: FileSearch,
    title: "The NSN's price history",
    body: "Past awards for the same NSN (who won, at what unit price and quantity) from the RFQ's procurement history and DLA award postings.",
  },
  {
    icon: BarChart3,
    title: "A calibrated winning-price range",
    body: "A quantile model predicts where the winning unit price will land, with ranges that are checked to cover the real outcome as often as they claim.",
  },
  {
    icon: Target,
    title: "The bid that pays best",
    body: "Your landed cost from supplier quotes, then the price that maximizes win probability × margin × quantity. You review it and submit on DIBBS yourself.",
  },
];

const FEATURES = [
  {
    icon: MailCheck,
    title: "Supplier quotes without the inbox work",
    body: "Draft RFQ emails to approved sources and past winners in one click. Replies are read automatically: price, lead time, minimums, validity.",
  },
  {
    icon: BadgeDollarSign,
    title: "Landed cost, not just unit cost",
    body: "Freight, packaging, inspection and first-article allowances, payment-term financing and overhead are folded in, and your minimum margin is enforced.",
  },
  {
    icon: ShieldCheck,
    title: "You stay in control",
    body: "Bid Copilot never submits anything to DIBBS. Every recommendation shows its reasoning, similar past awards and the risk of losing.",
  },
  {
    icon: Lock,
    title: "Your data stays yours",
    body: "Supplier contacts, quotes and bid history are isolated per company. Only public DLA data is shared across accounts.",
  },
];

export default function LandingPage() {
  const router = useRouter();
  const options = useAuthOptions();
  const health = useDataHealth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const live = health.data && health.data.connector_mode !== "fixture";
  const top = useQuery({
    queryKey: ["public-top-lines"],
    queryFn: async () => unwrap(await api.GET("/api/public/top-lines")),
    enabled: !!options.data?.public_demo,
    staleTime: 10 * 60_000,
    retry: false,
  });

  async function exploreDemo() {
    setBusy(true);
    setError(null);
    const { error } = await api.POST("/api/auth/demo");
    setBusy(false);
    if (error) return setError(errorMessage(error));
    router.replace("/dashboard");
    router.refresh();
  }

  return (
    <div>
      <section className="border-b bg-gradient-to-b from-primary/5 to-background">
        <div className="mx-auto max-w-6xl px-4 py-16 sm:py-24">
          <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-primary">For DLA DIBBS brokers and small suppliers</p>
          <h1 className="max-w-3xl text-3xl font-semibold tracking-tight sm:text-5xl">
            Price every DIBBS solicitation in minutes, not hours.
          </h1>
          <p className="mt-5 max-w-2xl text-base text-muted-foreground sm:text-lg">
            Bid Copilot reads each day&apos;s RFQs in your supply classes, pulls the NSN&apos;s award history, predicts the winning price,
            collects supplier quotes and recommends the bid with the best expected profit.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            {options.data?.public_demo && (
              <Button size="lg" onClick={exploreDemo} disabled={busy} data-testid="landing-demo">
                {busy ? "Opening the demo…" : "Explore the live demo"}
              </Button>
            )}
            <Button size="lg" variant={options.data?.public_demo ? "outline" : "default"} asChild>
              <Link href="/signup">Create your account</Link>
            </Button>
          </div>
          {error && <p className="mt-3 text-sm text-destructive">{error}</p>}
          {options.data?.public_demo && <p className="mt-3 text-xs text-muted-foreground">The demo is read-only and needs no sign-up.</p>}
          {live && health.data && (
            <dl className="mt-12 grid max-w-3xl grid-cols-2 gap-6 sm:grid-cols-4" data-testid="live-stats">
              <Stat label="Open RFQ lines tracked" value={num(health.data.open_rfq_lines)} />
              <Stat label="Award prices on file" value={num(health.data.award_rows)} />
              <Stat label="NSNs with price history" value={num(health.data.nsns_with_history)} />
              <Stat label="Newest DIBBS posting" value={formatDate(health.data.newest_rfq_posting_date)} />
            </dl>
          )}
        </div>
      </section>

      {health.data?.model?.real_data && health.data.model.median_abs_pct_error != null && health.data.model.test_rows ? (
        <section className="border-b bg-card">
          <div className="mx-auto grid max-w-6xl gap-6 px-4 py-10 sm:grid-cols-[1.2fr_2fr] sm:items-center" data-testid="accuracy">
            <div>
              <h2 className="text-xl font-semibold">Measured, not promised</h2>
              <p className="mt-2 text-sm text-muted-foreground">
                The pricing model is tested on the {num(health.data.model.test_rows)} most recent real DIBBS awards it never saw during
                training, and retrained every week as new awards post.
              </p>
            </div>
            <dl className="grid grid-cols-3 gap-4">
              <Stat label="Median price error" value={`${Math.round(health.data.model.median_abs_pct_error * 100)}%`} />
              <Stat
                label="Winning price inside the 80% range"
                value={health.data.model.coverage_80 != null ? `${Math.round(health.data.model.coverage_80 * 100)}%` : "—"}
              />
              <Stat
                label="Winning price inside the 90% range"
                value={health.data.model.coverage_90 != null ? `${Math.round(health.data.model.coverage_90 * 100)}%` : "—"}
              />
            </dl>
            {health.data.live_track_record && health.data.live_track_record.n >= 20 ? (
              <p className="text-xs text-muted-foreground sm:col-span-2" data-testid="live-track-record">
                Live since {formatDate(health.data.live_track_record.since)}: of {num(health.data.live_track_record.n)} solicitations priced
                before their award, the winning price fell inside the predicted 80% range {pct(health.data.live_track_record.coverage_80)}{" "}
                of the time (median error {pct(health.data.live_track_record.median_abs_pct_error)}).
              </p>
            ) : null}
          </div>
        </section>
      ) : null}

      {top.data && top.data.length > 0 && (
        <section className="mx-auto max-w-6xl px-4 pt-14" data-testid="top-lines">
          <h2 className="text-xl font-semibold">Live right now</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Three open DLA solicitations from today&apos;s data, priced the way the demo broker sees them.
          </p>
          <div className="mt-6 grid gap-4 md:grid-cols-3">
            {top.data.map((l) => (
              <div key={l.solicitation_number} className="rounded-lg border bg-card p-4">
                <div className="font-mono text-xs text-muted-foreground">{formatSolicitation(l.solicitation_number)}</div>
                <div className="mt-1 truncate text-sm font-semibold" title={l.nomenclature ?? ""}>
                  {l.nomenclature}
                </div>
                <div className="text-xs text-muted-foreground">
                  {num(l.quantity)} {l.unit_of_issue} · due {formatDate(l.return_by_date)}
                  {l.n_prior_awards ? ` · ${l.n_prior_awards} past awards` : ""}
                </div>
                <div className="mt-3 text-xs text-muted-foreground">Winning price likely</div>
                <div className="num text-lg font-semibold">{money(l.predicted_median)}</div>
                {l.range_low != null && l.range_high != null && (
                  <div className="text-xs text-muted-foreground">
                    80% range {money(l.range_low)}–{money(l.range_high)}
                  </div>
                )}
                <div className="mt-3 rounded-md bg-muted/60 px-2 py-1.5 text-xs">
                  Recommended bid <b className="num">{money(l.recommended_bid)}</b> · {pct(l.p_win)} to win
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="mx-auto max-w-6xl px-4 py-16">
        <h2 className="text-xl font-semibold">How it works</h2>
        <ol className="mt-8 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
          {STEPS.map(({ icon: Icon, title, body }, i) => (
            <li key={title} className="rounded-lg border bg-card p-5">
              <div className="mb-3 flex items-center gap-2 text-primary">
                <Icon className="h-5 w-5" />
                <span className="text-xs font-semibold">Step {i + 1}</span>
              </div>
              <h3 className="text-sm font-semibold">{title}</h3>
              <p className="mt-2 text-sm text-muted-foreground">{body}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="border-y bg-muted/30">
        <div className="mx-auto grid max-w-6xl gap-8 px-4 py-16 sm:grid-cols-2">
          {FEATURES.map(({ icon: Icon, title, body }) => (
            <div key={title} className="flex gap-4">
              <Icon className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
              <div>
                <h3 className="text-sm font-semibold">{title}</h3>
                <p className="mt-1.5 text-sm text-muted-foreground">{body}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-4 py-16 text-center">
        <h2 className="text-xl font-semibold">Start with the RFQs posted today</h2>
        <p className="mx-auto mt-3 max-w-xl text-sm text-muted-foreground">
          Create an account, pick your FSCs and margin floor, and today&apos;s open solicitations are priced within minutes.
        </p>
        <div className="mt-6 flex justify-center gap-3">
          <Button asChild>
            <Link href="/signup">Create your account</Link>
          </Button>
          {options.data?.public_demo && (
            <Button variant="outline" onClick={exploreDemo} disabled={busy}>
              Explore the demo first
            </Button>
          )}
        </div>
      </section>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-1 text-2xl font-semibold tabular-nums">{value}</dd>
    </div>
  );
}
