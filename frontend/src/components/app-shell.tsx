"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { Building2, CalendarClock, Eye, History, LogOut, Settings, ShieldCheck, Users } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge, Tip } from "@/components/ui/misc";
import { api } from "@/lib/api/client";
import { formatDate, timeAgo } from "@/lib/format";
import { useDataHealth, useMe } from "@/lib/hooks";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/dashboard", label: "Today", icon: CalendarClock },
  { href: "/history", label: "History", icon: History },
  { href: "/suppliers", label: "Suppliers", icon: Users },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const qc = useQueryClient();
  const { data: me } = useMe();

  async function endSession(to: string) {
    await api.POST("/api/auth/logout");
    qc.clear();
    router.replace(to);
    router.refresh();
  }
  const logout = () => endSession("/login");

  const nav = me?.user.is_superadmin ? [...NAV, { href: "/admin", label: "Admin", icon: ShieldCheck }] : NAV;

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-30 border-b bg-card/95 backdrop-blur">
        <div className="mx-auto flex h-12 max-w-[1600px] items-center gap-4 px-4">
          <Link href="/dashboard" className="flex items-center gap-2">
            <div className="flex h-6 w-6 items-center justify-center rounded bg-primary text-[10px] font-bold text-primary-foreground">
              DB
            </div>
            <span className="hidden text-sm font-semibold sm:inline">Bid Copilot</span>
          </Link>
          <nav className="flex items-center gap-0.5 overflow-x-auto">
            {nav.map(({ href, label, icon: Icon }) => (
              <Link
                key={href}
                href={href}
                className={cn(
                  "flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[13px] font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground",
                  (pathname === href || pathname.startsWith(`${href}/`) || (href === "/dashboard" && pathname.startsWith("/rfq"))) &&
                    "bg-accent text-foreground",
                )}
              >
                <Icon className="h-3.5 w-3.5" />
                {label}
              </Link>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <DataFreshness />
            {me?.organization.plan === "demo" && <Badge variant="warning">Demo org</Badge>}
            {me && (
              <div className="hidden items-center gap-1.5 text-xs text-muted-foreground md:flex" data-testid="org-name">
                <Building2 className="h-3.5 w-3.5" />
                <span className="max-w-[200px] truncate font-medium text-foreground">{me.organization.name}</span>
                <span>·</span>
                <span className="max-w-[180px] truncate">{me.user.email}</span>
              </div>
            )}
            <Button variant="ghost" size="sm" onClick={logout} aria-label="Log out">
              <LogOut />
              <span className="hidden sm:inline">Log out</span>
            </Button>
          </div>
        </div>
      </header>
      {me?.user.role === "viewer" && (
        <ReadOnlyBanner demo={me.organization.plan === "demo"} org={me.organization.name} onSignup={() => endSession("/signup")} />
      )}
      <main className="mx-auto w-full max-w-[1600px] flex-1 px-4 py-4">{children}</main>
    </div>
  );
}

/** "DIBBS data 40 min ago" with the health checks in a tooltip (live connectors only). */
function DataFreshness() {
  const { data } = useDataHealth();
  if (!data || data.connector_mode === "fixture") return null;
  const ok = data.status === "ok";
  return (
    <Tip
      content={
        <div className="space-y-1">
          <div>Newest DIBBS posting: {formatDate(data.newest_rfq_posting_date)}</div>
          <div>
            {data.open_rfq_lines.toLocaleString()} open lines · {data.award_rows.toLocaleString()} award prices on file
          </div>
          {data.checks
            .filter((c) => !c.ok)
            .map((c) => (
              <div key={c.name}>⚠ {c.name}</div>
            ))}
        </div>
      }
    >
      <span className="hidden items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] text-muted-foreground lg:flex" data-testid="data-freshness">
        <span className={cn("h-1.5 w-1.5 rounded-full", ok ? "bg-success" : "bg-warning")} />
        DIBBS data {timeAgo(data.runs["dibbs_rfq"]?.last_success_at ?? null)}
      </span>
    </Tip>
  );
}

function ReadOnlyBanner({ demo, org, onSignup }: { demo: boolean; org: string; onSignup: () => Promise<void> }) {
  return (
    <div className="border-b bg-primary/5" data-testid="read-only-banner">
      <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2 text-[13px]">
        <Eye className="h-4 w-4 text-primary" />
        {demo ? (
          <span>
            You&apos;re exploring a <strong>read-only demo</strong>: real DIBBS solicitations and award history, priced for a fictional
            broker. Supplier contacts and quotes in this demo are simulated.
          </span>
        ) : (
          <span>
            You have read-only access to <strong>{org}</strong>. Ask an owner for member access to make changes.
          </span>
        )}
        {demo && (
          <Button size="xs" className="ml-auto" onClick={() => void onSignup()}>
            Start your own account
          </Button>
        )}
      </div>
    </div>
  );
}
