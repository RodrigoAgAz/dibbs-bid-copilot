import Link from "next/link";
import { Brand } from "@/components/brand";

export const metadata = { title: "Page not found" };

export default function NotFound() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-muted/40 px-4 text-center">
      <Link href="/" className="flex items-center gap-2">
        <Brand size="md" />
      </Link>
      <h1 className="text-xl font-semibold">Page not found</h1>
      <p className="max-w-sm text-sm text-muted-foreground">The page you asked for doesn&apos;t exist. Solicitations live at /rfq/&lt;solicitation number&gt;.</p>
      <div className="flex gap-3 text-sm">
        <Link className="text-primary hover:underline" href="/dashboard">
          Today&apos;s solicitations
        </Link>
        <Link className="text-primary hover:underline" href="/">
          Home
        </Link>
      </div>
    </div>
  );
}
