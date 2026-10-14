"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, FormError } from "@/components/form";
import { api, errorMessage } from "@/lib/api/client";
import { useAuthOptions } from "@/lib/hooks";

export default function LoginPage() {
  const router = useRouter();
  const params = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const options = useAuthOptions();

  async function exploreDemo() {
    setBusy(true);
    setError(null);
    const { error } = await api.POST("/api/auth/demo");
    setBusy(false);
    if (error) return setError(errorMessage(error));
    router.replace("/dashboard");
    router.refresh();
  }

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    setBusy(true);
    setError(null);
    const { error } = await api.POST("/api/auth/login", {
      body: { email: String(form.get("email")), password: String(form.get("password")) },
    });
    setBusy(false);
    if (error) return setError(errorMessage(error));
    const next = params.get("next");
    router.replace(next && next.startsWith("/") ? next : "/dashboard");
    router.refresh();
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Log in</CardTitle>
        <CardDescription>Welcome back. Today&apos;s solicitations are waiting.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} className="grid gap-3">
          <Field id="email" label="Email" type="email" autoComplete="email" required />
          <Field id="password" label="Password" type="password" autoComplete="current-password" required />
          <FormError message={error} />
          <Button type="submit" disabled={busy}>
            {busy ? "Logging in…" : "Log in"}
          </Button>
          {options.data?.public_demo && (
            <>
              <div className="flex items-center gap-2 text-[11px] uppercase tracking-wide text-muted-foreground">
                <span className="h-px flex-1 bg-border" />
                or
                <span className="h-px flex-1 bg-border" />
              </div>
              <Button type="button" variant="outline" disabled={busy} onClick={exploreDemo} data-testid="explore-demo">
                Explore the live demo (no sign-up)
              </Button>
            </>
          )}
          <div className="flex justify-between text-xs">
            <Link className="text-primary hover:underline" href="/forgot-password">
              Forgot password?
            </Link>
            <Link className="text-primary hover:underline" href="/signup">
              Create an account
            </Link>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
