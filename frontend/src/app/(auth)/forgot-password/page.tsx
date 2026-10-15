"use client";

import Link from "next/link";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, FormError, FormNotice } from "@/components/form";
import { api, errorMessage } from "@/lib/api/client";

export default function ForgotPasswordPage() {
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true);
    setError(null);
    const { data, error } = await api.POST("/api/auth/forgot-password", {
      body: { email: String(f.get("email")) },
    });
    setBusy(false);
    if (error) return setError(errorMessage(error));
    setNotice(data?.detail ?? "Check your email.");
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle as="h1" className="text-base">Reset your password</CardTitle>
        <CardDescription>We&apos;ll email you a link to choose a new one.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} className="grid gap-3">
          <Field id="email" label="Email" type="email" autoComplete="email" required />
          <FormError message={error} />
          <FormNotice message={notice} />
          <Button type="submit" disabled={busy}>
            {busy ? "Sending…" : "Send reset link"}
          </Button>
          <Link className="text-center text-xs text-primary hover:underline" href="/login">
            Back to log in
          </Link>
        </form>
      </CardContent>
    </Card>
  );
}
