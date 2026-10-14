"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { BadgeDollarSign, BarChart3, FileSearch, Inbox, Lock, MailCheck, ShieldCheck, Target } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api, errorMessage } from "@/lib/api/client";
import { formatDate, num } from "@/lib/format";
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
