import Link from "next/link";
import { Brand } from "@/components/brand";
import { Suspense } from "react";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-muted/40 px-4 py-10">
      <Link href="/" className="mb-6 flex items-center gap-2">
        <Brand size="lg" sub="Winning prices for DLA DIBBS solicitations" />
      </Link>
      <div className="w-full max-w-sm">
        <Suspense>{children}</Suspense>
      </div>
      <p className="mt-6 max-w-sm text-center text-[11px] text-muted-foreground">
        Recommendations only. Bids are always reviewed and submitted by you on DIBBS.
        <br />
        <Link href="/terms" className="hover:underline">
          Terms
        </Link>{" "}
        ·{" "}
        <Link href="/privacy" className="hover:underline">
          Privacy
        </Link>{" "}
        · Not affiliated with DLA or the Department of Defense.
      </p>
    </div>
  );
}
