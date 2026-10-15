import Link from "next/link";
import { AUTH_PAGES } from "@/lib/flags";

export default function PublicLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col bg-background">
      <header className="border-b">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-4 px-4">
          <Link href="/" className="flex items-center gap-2">
            <div className="flex h-7 w-7 items-center justify-center rounded-md bg-primary text-[11px] font-bold text-primary-foreground">
              DB
            </div>
            <span className="text-sm font-semibold">DIBBS Bid Copilot</span>
          </Link>
          <nav className="ml-auto flex items-center gap-1 text-sm">
            {AUTH_PAGES ? (
              <>
                <Link href="/login" className="rounded-md px-3 py-1.5 text-muted-foreground hover:bg-accent hover:text-foreground">
                  Log in
                </Link>
                <Link href="/signup" className="rounded-md bg-primary px-3 py-1.5 font-medium text-primary-foreground hover:bg-primary/90">
                  Create account
                </Link>
              </>
            ) : (
              <Link href="/enter" className="rounded-md bg-primary px-3 py-1.5 font-medium text-primary-foreground hover:bg-primary/90">
                Open the app
              </Link>
            )}
          </nav>
        </div>
      </header>
      <main className="flex-1">{children}</main>
      <footer className="border-t bg-muted/30">
        <div className="mx-auto flex max-w-6xl flex-col gap-3 px-4 py-6 text-xs text-muted-foreground sm:flex-row sm:items-center sm:justify-between">
          <p className="max-w-2xl">
            DIBBS Bid Copilot is an independent product. It is not affiliated with, endorsed by, or connected to the Defense Logistics
            Agency (DLA) or the Department of Defense. It uses publicly posted DIBBS data and never submits bids.
          </p>
          <nav className="flex gap-4">
            <Link href="/terms" className="hover:text-foreground">
              Terms
            </Link>
            <Link href="/privacy" className="hover:text-foreground">
              Privacy
            </Link>
            {AUTH_PAGES && (
              <Link href="/login" className="hover:text-foreground">
                Log in
              </Link>
            )}
          </nav>
        </div>
      </footer>
    </div>
  );
}
