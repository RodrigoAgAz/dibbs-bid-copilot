"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, UserPlus, X } from "lucide-react";
import { ErrorState, Loading, PageHeader, WriteGuard } from "@/components/common";
import { FormError, FormNotice } from "@/components/form";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Textarea } from "@/components/ui/input";
import { Badge, Label, Switch } from "@/components/ui/misc";
import { Checkbox, NativeSelect } from "@/components/ui/overlay";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, errorMessage, unwrap, type Schemas } from "@/lib/api/client";
import { FSC_NAMES, formatDateTime } from "@/lib/format";
import { useMe } from "@/lib/hooks";

type S = Schemas["SettingsOut"];
const SET_ASIDES = [
  ["SB", "Small business"],
  ["WOSB", "Women-owned SB"],
  ["EDWOSB", "Econ. disadvantaged WOSB"],
  ["SDVOSB", "Service-disabled veteran-owned SB"],
  ["HUBZONE", "HUBZone"],
  ["8A", "8(a)"],
] as const;

function NumberField({ label, value, onChange, step = "0.01", suffix, hint }: { label: string; value: number; onChange: (v: number) => void; step?: string; suffix?: string; hint?: string }) {
  const id = label.replace(/\W+/g, "-").toLowerCase();
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <div className="flex items-center gap-1.5">
        <Input id={id} type="number" step={step} min="0" className="h-8 w-32 text-xs" value={Number.isFinite(value) ? value : ""} onChange={(e) => onChange(Number(e.target.value))} />
        {suffix && <span className="text-xs text-muted-foreground">{suffix}</span>}
      </div>
      {hint && <p className="text-[11px] text-muted-foreground">{hint}</p>}
    </div>
  );
}

function Team() {
  const qc = useQueryClient();
  const { data: me } = useMe();
  const members = useQuery({ queryKey: ["members"], queryFn: async () => unwrap(await api.GET("/api/org/members")) });
  const invites = useQuery({ queryKey: ["invites"], queryFn: async () => unwrap(await api.GET("/api/org/invitations")) });
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"member" | "owner" | "viewer">("member");
  const [link, setLink] = useState<string | null>(null);
  const invite = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/org/invitations", { body: { email, role } })),
    onSuccess: (r) => {
      setLink(r.invite_url);
      setEmail("");
      qc.invalidateQueries({ queryKey: ["invites"] });
    },
  });
  const owner = me?.user.role === "owner";
  return (
    <Card>
      <CardHeader>
        <CardTitle>Team</CardTitle>
        <CardDescription>Everyone in your organization sees the same watchlist, quotes and recommendations.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Table>
          <THead>
            <tr>
              <TH>Email</TH>
              <TH>Name</TH>
              <TH>Role</TH>
              <TH>Last login</TH>
            </tr>
          </THead>
          <TBody>
            {(members.data ?? []).map((m) => (
              <TR key={m.id}>
                <TD>{m.email}</TD>
                <TD>{m.full_name ?? "—"}</TD>
                <TD>
                  <Badge variant={m.role === "owner" ? "default" : "secondary"}>{m.role}</Badge>
                </TD>
                <TD className="text-[12px] text-muted-foreground">{formatDateTime(m.last_login_at)}</TD>
              </TR>
            ))}
            {(invites.data ?? [])
              .filter((i) => !i.accepted_at)
              .map((i) => (
                <TR key={`i${i.id}`}>
                  <TD className="text-muted-foreground">{i.email}</TD>
                  <TD className="text-muted-foreground">invited</TD>
                  <TD>
                    <Badge variant="outline">{i.role}</Badge>
                  </TD>
                  <TD className="text-[12px] text-muted-foreground">expires {formatDateTime(i.expires_at)}</TD>
                </TR>
              ))}
          </TBody>
        </Table>
        {owner ? (
          <form
            className="flex flex-wrap items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              invite.mutate();
            }}
          >
            <div className="grid gap-1">
              <Label htmlFor="invite-email">Invite by email</Label>
              <Input id="invite-email" type="email" required className="h-8 w-64 text-xs" value={email} onChange={(e) => setEmail(e.target.value)} />
            </div>
            <NativeSelect value={role} onChange={(e) => setRole(e.target.value as "member" | "owner" | "viewer")} aria-label="Role">
              <option value="member">Member</option>
              <option value="viewer">Viewer (read-only)</option>
              <option value="owner">Owner</option>
            </NativeSelect>
            <Button size="sm" type="submit" disabled={invite.isPending}>
              <UserPlus /> Send invite
            </Button>
          </form>
        ) : (
          <p className="text-xs text-muted-foreground">Only owners can invite teammates.</p>
        )}
        <FormError message={invite.error ? errorMessage(invite.error) : null} />
        {link && (
          <FormNotice message={`Invitation sent. With the console email provider you can share this link directly: ${link}`} />
        )}
      </CardContent>
    </Card>
  );
}

export default function SettingsPage() {
  const qc = useQueryClient();
  const { data: me } = useMe();
  const q = useQuery({ queryKey: ["settings"], queryFn: async () => unwrap(await api.GET("/api/settings")) });
  const [s, setS] = useState<S | null>(null);
  const [fscInput, setFscInput] = useState("");
  useEffect(() => {
    if (q.data) setS(q.data);
  }, [q.data]);
  const save = useMutation({
    mutationFn: async (body: S) => unwrap(await api.PUT("/api/settings", { body })),
    onSuccess: (data) => {
      setS(data);
      qc.setQueryData(["settings"], data);
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
  if (q.isLoading || !s) return q.error ? <ErrorState error={q.error} /> : <Loading />;
  const set = <K extends keyof S>(k: K, v: S[K]) => setS((cur) => (cur ? { ...cur, [k]: v } : cur));
  const addFsc = () => {
    const parts = fscInput.split(/[,\s]+/).filter((x) => /^\d{4}$/.test(x));
    if (parts.length) set("fsc_watchlist", Array.from(new Set([...s.fsc_watchlist, ...parts])).sort());
    setFscInput("");
  };

  return (
    <WriteGuard>
      <PageHeader
        title="Settings"
        description="Saving recalculates landed costs and recommendations for today's lines."
        actions={
          <Button onClick={() => save.mutate(s)} disabled={save.isPending} data-testid="save-settings">
            {save.isSuccess ? <Check /> : null} Save settings
          </Button>
        }
      />
      <FormError message={save.error ? errorMessage(save.error) : null} />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>FSC watchlist</CardTitle>
            <CardDescription>Federal Supply Classes you quote. New RFQs in these FSCs land on your dashboard every morning.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-wrap gap-1.5">
              {s.fsc_watchlist.map((f) => (
                <span key={f} className="inline-flex items-center gap-1 rounded-md border bg-muted px-2 py-0.5 text-xs">
                  <b>{f}</b> {FSC_NAMES[f] ?? ""}
                  <button aria-label={`Remove ${f}`} onClick={() => set("fsc_watchlist", s.fsc_watchlist.filter((x) => x !== f))}>
                    <X className="h-3 w-3" />
                  </button>
                </span>
              ))}
            </div>
            <div className="flex gap-2">
              <Input className="h-8 w-48 text-xs" placeholder="Add FSC, e.g. 5365" value={fscInput} onChange={(e) => setFscInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), addFsc())} />
              <Button size="sm" variant="outline" onClick={addFsc}>
                Add
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Margins &amp; limits</CardTitle>
            <CardDescription>Bids are never recommended below landed cost × (1 + minimum margin).</CardDescription>
          </CardHeader>
          <CardContent className="grid grid-cols-2 gap-4">
            <NumberField label="Minimum margin" value={Math.round(s.min_margin_pct * 1000) / 10} onChange={(v) => set("min_margin_pct", v / 100)} step="0.5" suffix="%" />
            <NumberField label="Max line value" value={s.max_line_value} onChange={(v) => set("max_line_value", v)} step="100" suffix="$" hint="No-bid above this" />
            <NumberField label="Minimum expected profit" value={s.min_expected_profit} onChange={(v) => set("min_expected_profit", v)} step="1" suffix="$" hint="No-bid below this" />
            <NumberField
              label="Minimum chance to win"
              value={Math.round(s.min_p_win * 100)}
              onChange={(v) => set("min_p_win", v / 100)}
              step="5"
              suffix="%"
              hint="Never recommend long-shot bids"
            />
            <NumberField
              label="Provisional cost estimate"
              value={Math.round(s.provisional_cost_fraction * 100)}
              onChange={(v) => set("provisional_cost_fraction", v / 100)}
              step="1"
              suffix="% of predicted price"
              hint="Used until a supplier quotes"
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Cost inputs</CardTitle>
            <CardDescription>landed = supplier price + freight + packaging + inspection + first article + financing + overhead</CardDescription>
          </CardHeader>
          <CardContent className="grid grid-cols-2 gap-4">
            <NumberField label="Overhead" value={Math.round(s.default_overhead_pct * 1000) / 10} onChange={(v) => set("default_overhead_pct", v / 100)} step="0.5" suffix="% of supplier price" />
            <NumberField label="Packaging per line" value={s.packaging_cost_per_line} onChange={(v) => set("packaging_cost_per_line", v)} suffix="$" />
            <NumberField label="Default freight per line" value={s.default_freight_per_line} onChange={(v) => set("default_freight_per_line", v)} suffix="$" hint="When the quote has no freight" />
            <NumberField label="Inspection allowance per line" value={s.inspection_allowance_per_line} onChange={(v) => set("inspection_allowance_per_line", v)} suffix="$" />
            <NumberField label="Origin inspection extra" value={s.origin_inspection_allowance_per_line} onChange={(v) => set("origin_inspection_allowance_per_line", v)} suffix="$" />
            <NumberField label="First article allowance" value={s.first_article_allowance_per_line} onChange={(v) => set("first_article_allowance_per_line", v)} suffix="$ per line" />
            <NumberField label="Payment terms" value={s.payment_terms_days} onChange={(v) => set("payment_terms_days", Math.round(v))} step="1" suffix="days" />
            <NumberField label="Cost of capital" value={Math.round(s.cost_of_capital_pct * 1000) / 10} onChange={(v) => set("cost_of_capital_pct", v / 100)} step="0.5" suffix="% / year" />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Set-aside eligibility</CardTitle>
            <CardDescription>Lines set aside for programs you&apos;re not eligible for are marked no-bid.</CardDescription>
          </CardHeader>
          <CardContent className="grid grid-cols-2 gap-2">
            {SET_ASIDES.map(([code, label]) => (
              <Checkbox
                key={code}
                label={label}
                checked={s.set_aside_eligibility.includes(code)}
                onChange={(e) =>
                  set("set_aside_eligibility", e.target.checked ? [...s.set_aside_eligibility, code] : s.set_aside_eligibility.filter((x) => x !== code))
                }
              />
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Supplier outreach</CardTitle>
            <CardDescription>RFQ emails go out with a unique reply-to address per request, so replies parse automatically.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-1.5">
              <Label htmlFor="sender">Sender name</Label>
              <Input id="sender" className="h-8 text-xs" value={s.email_sender_name ?? ""} onChange={(e) => set("email_sender_name", e.target.value || null)} placeholder={me?.organization.name} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="signature">Email signature</Label>
              <Textarea id="signature" className="min-h-[70px] text-xs" value={s.email_signature ?? ""} onChange={(e) => set("email_signature", e.target.value || null)} />
            </div>
            <label className="flex items-center justify-between gap-3 text-sm">
              <span>
                Auto-send RFQ emails
                <span className="block text-xs text-muted-foreground">
                  Send to candidate suppliers with an email address as soon as new lines arrive. Off: draft and send from each RFQ page.
                </span>
              </span>
              <Switch checked={s.auto_send_rfq} onCheckedChange={(v) => set("auto_send_rfq", v)} aria-label="Auto-send RFQ emails" />
            </label>
            {me?.voice_globally_enabled && (
              <label className="flex items-center justify-between gap-3 text-sm">
                <span>
                  AI voice calls
                  <span className="block text-xs text-muted-foreground">
                    Landlines or consenting contacts only, supplier business hours, max one call per supplier per day. The assistant discloses it&apos;s an AI.
                  </span>
                </span>
                <Switch checked={s.voice_enabled} onCheckedChange={(v) => set("voice_enabled", v)} aria-label="AI voice calls" />
              </label>
            )}
          </CardContent>
        </Card>

        <Notifications />
        <Team />
      </div>
    </WriteGuard>
  );
}

/** Personal preferences (saved immediately, per user). */
function Notifications() {
  const qc = useQueryClient();
  const { data: me } = useMe();
  const save = useMutation({
    mutationFn: async (email_digest: boolean) => unwrap(await api.PUT("/api/auth/me/preferences", { body: { email_digest } })),
    onSuccess: (data) => qc.setQueryData(["me"], data),
  });
  if (!me) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Notifications</CardTitle>
        <CardDescription>Your personal email preferences.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        <label className="flex items-center justify-between gap-3 text-sm">
          <span>
            Daily digest email
            <span className="block text-xs text-muted-foreground">
              Weekday mornings: the most profitable new lines, bid-worthy lines closing within two days, and quotes waiting for review.
            </span>
          </span>
          <Switch
            checked={me.user.email_digest ?? true}
            onCheckedChange={(v) => save.mutate(v)}
            disabled={save.isPending}
            aria-label="Daily digest email"
          />
        </label>
        <FormError message={save.error ? errorMessage(save.error) : null} />
      </CardContent>
    </Card>
  );
}
