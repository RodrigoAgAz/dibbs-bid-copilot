"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, FormError } from "@/components/form";
import { api, errorMessage } from "@/lib/api/client";

export default function SignupPage() {
  const router = useRouter();
  const invite = useSearchParams().get("invite");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true);
    setError(null);
    const fullName = String(f.get("full_name") || "") || null;
    const { error } = invite
      ? await api.POST("/api/auth/accept-invite", {
          body: { token: invite, full_name: fullName, password: String(f.get("password")) },
        })
      : await api.POST("/api/auth/signup", {
          body: {
            organization_name: String(f.get("organization_name")),
            full_name: fullName,
            email: String(f.get("email")),
            password: String(f.get("password")),
            cage_code: String(f.get("cage_code") || "") || null,
          },
        });
    setBusy(false);
    if (error) return setError(errorMessage(error));
    router.replace("/dashboard");
    router.refresh();
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{invite ? "Join your team" : "Create your account"}</CardTitle>
        <CardDescription>
          {invite
            ? "Set a password to accept the invitation."
            : "Sets up your firm's workspace. You'll be its owner and can invite teammates."}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} className="grid gap-3">
          {!invite && (
            <>
              <Field id="organization_name" label="Company name" required minLength={2} />
              <Field id="cage_code" label="Your CAGE code (optional)" maxLength={5} />
            </>
          )}
          <Field id="full_name" label="Your name" autoComplete="name" />
          {!invite && <Field id="email" label="Work email" type="email" autoComplete="email" required />}
          <Field
            id="password"
            label="Password"
            type="password"
            autoComplete="new-password"
            required
            minLength={8}
            hint="At least 8 characters."
          />
          <FormError message={error} />
          <Button type="submit" disabled={busy}>
            {busy ? "Creating…" : invite ? "Accept invitation" : "Create account"}
          </Button>
          <p className="text-center text-[11px] text-muted-foreground">
            By continuing you agree to the{" "}
            <Link className="underline" href="/terms" target="_blank">
              Terms
            </Link>{" "}
            and{" "}
            <Link className="underline" href="/privacy" target="_blank">
              Privacy Policy
            </Link>
            .
          </p>
          <p className="text-center text-xs text-muted-foreground">
            Already have an account?{" "}
            <Link className="text-primary hover:underline" href="/login">
              Log in
            </Link>
          </p>
        </form>
      </CardContent>
    </Card>
  );
}
