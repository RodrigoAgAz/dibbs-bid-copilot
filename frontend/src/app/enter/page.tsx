"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/misc";
import { api, errorMessage } from "@/lib/api/client";

/** Opens the read-only demo session and continues to the requested page (no login screen during development). */
function Enter() {
  const router = useRouter();
  const params = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  async function go() {
    setError(null);
    const { error } = await api.POST("/api/auth/demo");
    if (error) return setError(errorMessage(error));
    const next = params.get("next");
    router.replace(next && next.startsWith("/") && !next.startsWith("//") ? next : "/dashboard");
    router.refresh();
  }

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void go();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-muted/40 px-4 text-center">
      {error ? (
        <>
          <p className="text-sm text-destructive">{error}</p>
          <Button size="sm" onClick={() => void go()}>
            Try again
          </Button>
        </>
      ) : (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Spinner /> Opening The Invisible Hand…
        </div>
      )}
    </div>
  );
}

export default function EnterPage() {
  return (
    <Suspense>
      <Enter />
    </Suspense>
  );
}
