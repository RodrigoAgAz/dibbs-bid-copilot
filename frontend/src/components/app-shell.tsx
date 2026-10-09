"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { Building2, CalendarClock, History, LogOut, Settings, ShieldCheck, Users } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/misc";
import { api } from "@/lib/api/client";
import { useMe } from "@/lib/hooks";
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

  async function logout() {
    await api.POST("/api/auth/logout");
    qc.clear();
    router.replace("/login");
    router.refresh();
  }

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
            {me?.demo_mode && <Badge variant="warning">Demo data</Badge>}
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
      <main className="mx-auto w-full max-w-[1600px] flex-1 px-4 py-4">{children}</main>
    </div>
  );
}
