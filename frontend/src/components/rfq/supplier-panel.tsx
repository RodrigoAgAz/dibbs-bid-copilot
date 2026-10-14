"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Mail, Phone, Plus, Send, Sparkles, Wand2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { Badge, Label, Tip } from "@/components/ui/misc";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { WriteGuard } from "@/components/common";
import { FormError } from "@/components/form";
import { api, errorMessage, unwrap, type Schemas } from "@/lib/api/client";
import { formatDateTime, money, num, pct } from "@/lib/format";
import { useMe } from "@/lib/hooks";
import { cn } from "@/lib/utils";

type Candidate = Schemas["CandidateOut"];
type QR = Schemas["QuoteRequestOut"];
type Quote = Schemas["SupplierQuoteOut"];

const REASON: Record<string, string> = {
  approved_source: "Approved source",
  historical: "Past awardee",
  saved: "Saved supplier",
  manual: "Added manually",
  voice: "Voice",
};

const STATUS_VARIANT: Record<string, "default" | "secondary" | "success" | "warning" | "destructive" | "outline"> = {
  draft: "secondary",
  sent: "default",
  replied: "default",
  parsed: "success",
  declined: "outline",
  no_response: "outline",
  failed: "destructive",
};

function useInvalidate(sn: string) {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: ["rfq", sn] });
    qc.invalidateQueries({ queryKey: ["dashboard"] });
  };
}

function QuoteRow({ q, sn }: { q: Quote; sn: string }) {
  const invalidate = useInvalidate(sn);
  const [edit, setEdit] = useState(false);
  const [price, setPrice] = useState(String(q.unit_price ?? ""));
  const [lead, setLead] = useState(String(q.lead_time_days ?? ""));
  const [moq, setMoq] = useState(String(q.min_order_qty ?? ""));
  const [raw, setRaw] = useState(false);
  const save = useMutation({
    mutationFn: async (body: Schemas["QuotePatch"]) =>
      unwrap(await api.PATCH("/api/supplier-quotes/{quote_id}", { params: { path: { quote_id: q.id } }, body })),
    onSuccess: () => {
      setEdit(false);
      invalidate();
    },
  });
  return (
    <div className="rounded-md border bg-background p-2 text-xs" data-testid="supplier-quote">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        {q.declined ? (
          <Badge variant="outline">Declined</Badge>
        ) : (
          <span className="num text-sm font-semibold">{money(q.unit_price)}</span>
        )}
        <span>Lead {q.lead_time_days != null ? `${q.lead_time_days} d` : "—"}</span>
        <span>MOQ {q.min_order_qty != null ? num(q.min_order_qty) : "—"}</span>
        <span>P/N {q.quoted_part_number ?? "—"}</span>
        <span>Valid {q.quote_valid_until ?? "—"}</span>
        <span>Freight {q.freight_cost != null ? money(q.freight_cost) : q.freight_terms ?? "—"}</span>
        {q.certs_offered.length > 0 && <span>{q.certs_offered.join(", ")}</span>}
        <Badge variant={q.conforms_to_part_number ? "success" : "destructive"}>{q.conforms_to_part_number ? "Conforming" : "Non-conforming"}</Badge>
        <Tip content="Extraction confidence">
          <span className="text-muted-foreground">conf. {pct(q.extraction_confidence)}</span>
        </Tip>
        <span className="text-muted-foreground">via {q.source}</span>
        {q.needs_review && !q.reviewed_at && (
          <Tip content={q.review_reason ?? "Needs review"}>
            <span>
              <Badge variant="warning">Needs review</Badge>
            </span>
          </Tip>
        )}
        <div className="ml-auto flex gap-1">
          {q.needs_review && !q.reviewed_at && (
            <Button size="xs" variant="outline" onClick={() => save.mutate({ mark_reviewed: true })} disabled={save.isPending}>
              Approve
            </Button>
          )}
          <Button size="xs" variant="ghost" onClick={() => setEdit((v) => !v)}>
            Edit
          </Button>
          {q.raw_text && (
            <Button size="xs" variant="ghost" onClick={() => setRaw((v) => !v)}>
              {raw ? "Hide text" : "Source text"}
            </Button>
          )}
        </div>
      </div>
      {q.needs_review && q.review_reason && !q.reviewed_at && <p className="mt-1 text-[11px] text-warning">{q.review_reason}</p>}
      {edit && (
        <form
          className="mt-2 flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate({
              unit_price: price ? Number(price) : undefined,
              lead_time_days: lead ? Number(lead) : null,
              min_order_qty: moq ? Number(moq) : null,
              mark_reviewed: true,
            });
          }}
        >
          <div className="grid gap-1">
            <Label htmlFor={`p-${q.id}`}>Unit price</Label>
            <Input id={`p-${q.id}`} className="h-7 w-24 text-xs" type="number" step="0.0001" min="0" value={price} onChange={(e) => setPrice(e.target.value)} />
          </div>
          <div className="grid gap-1">
            <Label htmlFor={`l-${q.id}`}>Lead days</Label>
            <Input id={`l-${q.id}`} className="h-7 w-20 text-xs" type="number" min="0" value={lead} onChange={(e) => setLead(e.target.value)} />
          </div>
          <div className="grid gap-1">
            <Label htmlFor={`m-${q.id}`}>MOQ</Label>
            <Input id={`m-${q.id}`} className="h-7 w-20 text-xs" type="number" min="0" value={moq} onChange={(e) => setMoq(e.target.value)} />
          </div>
          <Button size="xs" type="submit" disabled={save.isPending}>
            Save &amp; mark reviewed
          </Button>
          <FormError message={save.error ? errorMessage(save.error) : null} />
        </form>
      )}
      {raw && <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-muted p-2 text-[11px]">{q.raw_text}</pre>}
    </div>
  );
}

function QuoteRequestDetail({ qr, sn, demo }: { qr: QR; sn: string; demo: boolean }) {
  const quotes = qr.quotes ?? [];
  const calls = qr.calls ?? [];
  const emails = qr.emails ?? [];
  const invalidate = useInvalidate(sn);
  const [to, setTo] = useState(qr.email_to ?? "");
  const [subject, setSubject] = useState(qr.email_subject ?? "");
  const [body, setBody] = useState(qr.email_body ?? "");
  const [reply, setReply] = useState("");
  const [manualPrice, setManualPrice] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const id = qr.id;
  const run = useMutation({
    mutationFn: async (fn: () => Promise<unknown>) => fn(),
    onSuccess: () => {
      setErr(null);
      invalidate();
    },
    onError: (e) => setErr(errorMessage(e)),
  });
  const path = { params: { path: { qr_id: id } } };
  const saveDraft = () => run.mutate(async () => unwrap(await api.PATCH("/api/quote-requests/{qr_id}", { ...path, body: { email_to: to || null, email_subject: subject, email_body: body } })));
  const isDraft = qr.status === "draft";

  return (
    <div className="space-y-3 rounded-md border bg-muted/30 p-3">
      {qr.channel === "email" && (
        <div className="grid gap-2">
          <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
            <Mail className="h-3.5 w-3.5" /> Replies route to <code className="rounded bg-muted px-1">{qr.reply_address}</code>
            {qr.followup_count > 0 && <Badge variant="secondary">{qr.followup_count} follow-up</Badge>}
            {qr.sent_at && <span>sent {formatDateTime(qr.sent_at)}</span>}
            {qr.error && <span className="text-destructive">{qr.error}</span>}
          </div>
          <div className="grid gap-2 md:grid-cols-[1fr_2fr]">
            <div className="grid gap-1">
              <Label htmlFor={`to-${id}`}>To</Label>
              <Input id={`to-${id}`} className="h-8 text-xs" placeholder="supplier@example.com" value={to} onChange={(e) => setTo(e.target.value)} disabled={!isDraft} />
            </div>
            <div className="grid gap-1">
              <Label htmlFor={`sub-${id}`}>Subject</Label>
              <Input id={`sub-${id}`} className="h-8 text-xs" value={subject} onChange={(e) => setSubject(e.target.value)} disabled={!isDraft} />
            </div>
          </div>
          <Textarea className="min-h-[180px] font-mono text-[11px]" value={body} onChange={(e) => setBody(e.target.value)} disabled={!isDraft} aria-label="Email body" />
          <div className="flex flex-wrap gap-1.5">
            {isDraft && (
              <>
                <Button size="sm" variant="outline" onClick={saveDraft} disabled={run.isPending}>
                  Save draft
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() =>
                    run.mutate(async () => {
                      const r = unwrap(await api.POST("/api/quote-requests/{qr_id}/draft", path));
                      setSubject(r.email_subject ?? "");
                      setBody(r.email_body ?? "");
                    })
                  }
                  disabled={run.isPending}
                >
                  <Wand2 /> Polish with AI
                </Button>
              </>
            )}
            <Button
              size="sm"
              onClick={() =>
                run.mutate(async () => {
                  if (isDraft) unwrap(await api.PATCH("/api/quote-requests/{qr_id}", { ...path, body: { email_to: to || null, email_subject: subject, email_body: body } }));
                  return unwrap(await api.POST("/api/quote-requests/{qr_id}/send", path));
                })
              }
              disabled={run.isPending || !to}
              data-testid="send-rfq"
            >
              <Send /> {isDraft ? "Send RFQ email" : "Send follow-up"}
            </Button>
          </div>
        </div>
      )}

      {quotes.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Parsed quotes</div>
          {quotes.map((q) => (
            <QuoteRow key={q.id} q={q} sn={sn} />
          ))}
        </div>
      )}

      {calls.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Calls</div>
          {calls.map((c) => (
            <details key={c.id} className="rounded-md border bg-background p-2 text-xs">
              <summary className="cursor-pointer">
                {formatDateTime(c.started_at)} · {c.outcome ?? "in progress"} · disclosure {c.disclosure_played ? "played" : "not confirmed"}
              </summary>
              <pre className="mt-2 whitespace-pre-wrap text-[11px]">{c.transcript ?? "Transcript pending."}</pre>
            </details>
          ))}
        </div>
      )}

      {emails.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Email thread</div>
          {emails.map((e) => (
            <details key={e.id} className={cn("rounded-md border p-2 text-xs", e.direction === "inbound" ? "bg-card" : "bg-background")}>
              <summary className="cursor-pointer">
                <Badge variant={e.direction === "inbound" ? "success" : "secondary"}>{e.direction === "inbound" ? "Reply" : e.kind}</Badge>{" "}
                {formatDateTime(e.created_at)} · {e.direction === "inbound" ? e.from_addr : e.to_addr} · {e.subject}
              </summary>
              <pre className="mt-2 whitespace-pre-wrap text-[11px]">{e.body}</pre>
            </details>
          ))}
        </div>
      )}

      <div className="flex flex-wrap items-end gap-2 border-t pt-2">
        <div className="grid gap-1">
          <Label htmlFor={`mq-${id}`}>Got a quote by phone? Unit price</Label>
          <Input id={`mq-${id}`} className="h-7 w-28 text-xs" type="number" step="0.0001" min="0" value={manualPrice} onChange={(e) => setManualPrice(e.target.value)} />
        </div>
        <Button
          size="xs"
          variant="outline"
          disabled={!manualPrice || run.isPending}
          onClick={() =>
            run.mutate(async () => {
              unwrap(await api.POST("/api/quote-requests/{qr_id}/quotes", { ...path, body: { unit_price: Number(manualPrice), certs_offered: [] } }));
              setManualPrice("");
            })
          }
        >
          <Plus /> Add quote
        </Button>
        {demo && (
          <div className="flex flex-1 flex-wrap items-end gap-2">
            <div className="grid flex-1 gap-1">
              <Label htmlFor={`sim-${id}`}>Demo: simulate the supplier&apos;s reply</Label>
              <Input
                id={`sim-${id}`}
                className="h-7 text-xs"
                placeholder="e.g. Unit price $4.20 each, lead time 3 weeks ARO, quote valid 30 days"
                value={reply}
                onChange={(e) => setReply(e.target.value)}
              />
            </div>
            <Button
              size="xs"
              variant="outline"
              disabled={!reply || run.isPending}
              onClick={() =>
                run.mutate(async () => {
                  unwrap(await api.POST("/api/quote-requests/{qr_id}/simulate-reply", { ...path, body: { text: reply } }));
                  setReply("");
                })
              }
            >
              <Sparkles /> Parse reply
            </Button>
          </div>
        )}
      </div>
      <FormError message={err} />
    </div>
  );
}

export function SupplierPanel({
  lineId,
  sn,
  candidates,
  voiceEnabled,
  demo,
}: {
  lineId: number;
  sn: string;
  candidates: Candidate[];
  voiceEnabled: boolean;
  demo: boolean;
}) {
  const invalidate = useInvalidate(sn);
  const [open, setOpen] = useState<string | null>(null);
  const [cage, setCage] = useState("");
  const [email, setEmail] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const add = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST("/api/rfq/lines/{line_id}/quote-requests", { params: { path: { line_id: lineId } }, body: { cage_code: cage, email_to: email || null } })),
    onSuccess: () => {
      setCage("");
      setEmail("");
      setErr(null);
      invalidate();
    },
    onError: (e) => setErr(errorMessage(e)),
  });
  const draft = useMutation({
    mutationFn: async (cages: string[] | null) =>
      unwrap(
        await api.POST("/api/rfq/lines/{line_id}/quote-requests/draft", {
          params: { path: { line_id: lineId } },
          body: { cage_codes: cages },
        }),
      ),
    onSuccess: (created) => {
      setErr(null);
      if (created.length === 1) setOpen(created[0].cage_code);
      invalidate();
    },
    onError: (e) => setErr(errorMessage(e)),
  });
  const undrafted = candidates.filter((c) => !c.quote_requests.some((q) => q.channel === "email"));
  const { data: me } = useMe();
  const call = useMutation({
    mutationFn: async (c: Candidate) =>
      unwrap(
        await api.POST("/api/rfq/lines/{line_id}/call", {
          params: { path: { line_id: lineId } },
          body: { supplier_contact_id: c.supplier_contact_id!, part_number: c.part_numbers[0] ?? null },
        }),
      ),
    onSuccess: () => invalidate(),
    onError: (e) => setErr(errorMessage(e)),
  });

  return (
    <WriteGuard className="space-y-2">
      {me && !me.email_delivery && me.organization.plan !== "demo" && (
        <p className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs" data-testid="email-not-configured">
          Email delivery isn&apos;t set up on this deployment yet: <strong>Send</strong> records the request, but nothing reaches the
          supplier. Copy the draft into your own email for now.
        </p>
      )}
      {undrafted.length > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
          <span>
            {undrafted.length} candidate supplier{undrafted.length === 1 ? "" : "s"} without a quote request. Drafts are only sent when
            you click Send.
          </span>
          <Button size="xs" variant="outline" disabled={draft.isPending} onClick={() => draft.mutate(null)} data-testid="draft-all">
            <Plus /> Draft requests for all {undrafted.length}
          </Button>
        </div>
      )}
      <div className="overflow-hidden rounded-md border">
        <Table>
          <THead>
            <tr>
              <TH className="w-6" />
              <TH>CAGE</TH>
              <TH>Supplier</TH>
              <TH>Why</TH>
              <TH>Contact</TH>
              <TH>Status</TH>
              <TH className="text-right">Best quote</TH>
              <TH />
            </tr>
          </THead>
          <TBody>
            {candidates.map((c) => {
              const quotes = c.quote_requests.flatMap((q) => q.quotes ?? []).filter((q) => q.unit_price != null && !q.declined);
              const best = quotes.length ? Math.min(...quotes.map((q) => q.unit_price!)) : null;
              const isOpen = open === c.cage_code;
              return (
                <FragmentRow key={c.cage_code}>
                  <TR className="cursor-pointer" onClick={() => setOpen(isOpen ? null : c.cage_code)} data-testid="supplier-row">
                    <TD>{isOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}</TD>
                    <TD className="font-mono text-[12px]">{c.cage_code}</TD>
                    <TD className="max-w-[220px] truncate" title={c.company_name ?? ""}>
                      {c.company_name ?? <span className="text-muted-foreground">Unknown</span>}
                      {c.part_numbers.length > 0 && <div className="text-[11px] text-muted-foreground">P/N {c.part_numbers.join(", ")}</div>}
                    </TD>
                    <TD>
                      <Badge variant={c.reason === "approved_source" ? "default" : "secondary"}>{REASON[c.reason] ?? c.reason}</Badge>
                      {c.historical_awards > 0 && <span className="ml-1 text-[11px] text-muted-foreground">{c.historical_awards} wins</span>}
                    </TD>
                    <TD className="text-[12px]">
                      {c.email ?? <span className="text-muted-foreground">no email</span>}
                      {c.phone && <div className="text-[11px] text-muted-foreground">{c.phone} · {c.phone_type}</div>}
                    </TD>
                    <TD>
                      {c.quote_requests.length ? (
                        c.quote_requests.map((q) => (
                          <Badge key={q.id} variant={STATUS_VARIANT[q.status] ?? "secondary"} className="mr-1">
                            {q.channel === "voice" ? "call " : ""}
                            {q.status.replace("_", " ")}
                          </Badge>
                        ))
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </TD>
                    <TD className="num text-right font-medium">{money(best)}</TD>
                    <TD className="text-right" onClick={(e) => e.stopPropagation()}>
                      {voiceEnabled && (
                        <Tip content={c.callable ? "Call with the AI assistant (discloses it is an AI, asks consent to record)" : c.call_block_reasons.join("; ")}>
                          <span>
                            <Button size="xs" variant="outline" disabled={!c.callable || call.isPending} onClick={() => call.mutate(c)}>
                              <Phone /> Call
                            </Button>
                          </span>
                        </Tip>
                      )}
                    </TD>
                  </TR>
                  {isOpen && (
                    <tr>
                      <td colSpan={8} className="bg-muted/20 p-2">
                        {c.quote_requests.length ? (
                          <div className="space-y-2">
                            {c.quote_requests.map((q) => (
                              <QuoteRequestDetail key={q.id} qr={q} sn={sn} demo={demo} />
                            ))}
                          </div>
                        ) : (
                          <div className="flex items-center gap-3 p-2 text-xs text-muted-foreground">
                            No quote request for this supplier yet.
                            <Button size="xs" variant="outline" disabled={draft.isPending} onClick={() => draft.mutate([c.cage_code])}>
                              <Plus /> Draft quote request
                            </Button>
                          </div>
                        )}
                      </td>
                    </tr>
                  )}
                </FragmentRow>
              );
            })}
          </TBody>
        </Table>
      </div>
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          add.mutate();
        }}
      >
        <div className="grid gap-1">
          <Label htmlFor={`cage-${lineId}`}>Add a supplier (CAGE)</Label>
          <Input id={`cage-${lineId}`} className="h-8 w-28 font-mono text-xs uppercase" maxLength={5} value={cage} onChange={(e) => setCage(e.target.value.toUpperCase())} />
        </div>
        <div className="grid gap-1">
          <Label htmlFor={`em-${lineId}`}>Email</Label>
          <Input id={`em-${lineId}`} className="h-8 w-56 text-xs" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <Button size="sm" variant="outline" type="submit" disabled={cage.length !== 5 || add.isPending}>
          <Plus /> Draft RFQ
        </Button>
      </form>
      <FormError message={err} />
    </WriteGuard>
  );
}

function FragmentRow({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
