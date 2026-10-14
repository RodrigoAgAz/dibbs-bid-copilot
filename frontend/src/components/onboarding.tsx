"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, Circle, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api, unwrap } from "@/lib/api/client";
import { useMe } from "@/lib/hooks";

const KEY = "onboarding-dismissed";
const NEW_ACCOUNT_DAYS = 30;

/** First-run checklist for owners of new organizations; hidden once dismissed or complete. */
export function OnboardingChecklist() {
  const { data: me } = useMe();
  const [dismissed, setDismissed] = useState(true);
  useEffect(() => {
    try {
      setDismissed(window.localStorage.getItem(KEY) === "1");
    } catch {
      setDismissed(false);
    }
  }, []);
  const eligible =
    !!me &&
    me.user.role === "owner" &&
    me.organization.plan !== "demo" &&
    Date.now() - new Date(me.user.created_at).getTime() < NEW_ACCOUNT_DAYS * 86_400_000;
  const suppliers = useQuery({
    queryKey: ["suppliers", ""],
    queryFn: async () => unwrap(await api.GET("/api/suppliers")),
    enabled: eligible && !dismissed,
  });
  const members = useQuery({
    queryKey: ["members"],
    queryFn: async () => unwrap(await api.GET("/api/org/members")),
    enabled: eligible && !dismissed,
  });
  if (!eligible || dismissed || !me) return null;

  const steps = [
    { done: !!me.organization.cage_code, label: "Add your CAGE code", hint: "Used in RFQ emails to suppliers.", href: "/settings" },
    {
      done: false,
      label: "Review your FSC watchlist, margin floor and costs",
      hint: "Recommendations are recomputed as soon as you save.",
      href: "/settings",
    },
    {
      done: (suppliers.data?.length ?? 0) > 0,
      label: "Add the suppliers you buy from",
      hint: "Approved sources and past winners show up on every RFQ automatically; your contacts add emails and phone numbers.",
      href: "/suppliers",
    },
    {
      done: (members.data?.length ?? 0) > 1,
      label: "Invite your team",
      hint: "Members can work RFQs; viewers are read-only.",
      href: "/settings",
    },
  ];
  const left = steps.filter((s) => !s.done).length;

  function dismiss() {
    setDismissed(true);
    try {
      window.localStorage.setItem(KEY, "1");
    } catch {
      /* private mode: dismissed for this visit only */
    }
  }

  return (
    <div className="mb-4 rounded-lg border bg-card p-4" data-testid="onboarding">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">Get set up</h2>
          <p className="text-xs text-muted-foreground">
            Today&apos;s open lines are already priced with default settings. {left} step{left === 1 ? "" : "s"} left to make them yours.
          </p>
        </div>
        <Button variant="ghost" size="xs" onClick={dismiss} aria-label="Dismiss setup checklist">
          <X />
        </Button>
      </div>
      <ul className="grid gap-2 sm:grid-cols-2">
        {steps.map((s) => (
          <li key={s.label} className="flex gap-2">
            {s.done ? (
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" />
            ) : (
              <Circle className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
            )}
            <div className="text-sm">
              <Link href={s.href} className={s.done ? "text-muted-foreground line-through" : "font-medium text-primary hover:underline"}>
                {s.label}
              </Link>
              <p className="text-xs text-muted-foreground">{s.hint}</p>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
