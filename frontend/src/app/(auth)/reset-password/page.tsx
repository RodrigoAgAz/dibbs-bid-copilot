"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, FormError, FormNotice } from "@/components/form";
import { api, errorMessage } from "@/lib/api/client";

export default function ResetPasswordPage() {
  const token = useSearchParams().get("token") ?? "";
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    if (f.get("password") !== f.get("confirm")) return setError("Passwords don't match.");
    setBusy(true);
    setError(null);
    const { data, error } = await api.POST("/api/auth/reset-password", {
      body: { token, password: String(f.get("password")) },
    });
    setBusy(false);
    if (error) return setError(errorMessage(error));
    setNotice(data?.detail ?? "Password updated.");
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Choose a new password</CardTitle>
        <CardDescription>All other sessions will be signed out.</CardDescription>
      </CardHeader>
      <CardContent>
        {!token ? (
          <FormError message="This reset link is missing its token. Request a new one." />
        ) : (
          <form onSubmit={onSubmit} className="grid gap-3">
            <Field id="password" label="New password" type="password" autoComplete="new-password" minLength={8} required />
            <Field id="confirm" label="Confirm password" type="password" autoComplete="new-password" minLength={8} required />
            <FormError message={error} />
            <FormNotice message={notice} />
            <Button type="submit" disabled={busy || Boolean(notice)}>
              {busy ? "Saving…" : "Set password"}
            </Button>
          </form>
        )}
        <Link className="mt-3 block text-center text-xs text-primary hover:underline" href="/login">
          Back to log in
        </Link>
      </CardContent>
    </Card>
  );
}
