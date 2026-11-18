"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Search, Trash2 } from "lucide-react";
import { Empty, ErrorState, Loading, PageHeader, WriteGuard } from "@/components/common";
import { formatPhone } from "@/lib/format";
import { useReadOnly } from "@/lib/hooks";
import { Field, FormError } from "@/components/form";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { Badge, Label } from "@/components/ui/misc";
import { Checkbox, Dialog, DialogContent, NativeSelect } from "@/components/ui/overlay";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, errorMessage, unwrap, type Schemas } from "@/lib/api/client";

type Supplier = Schemas["SupplierOut"];

function SupplierForm({ initial, onDone }: { initial?: Supplier; onDone: () => void }) {
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: async (f: FormData) => {
      const body = {
        company_name: String(f.get("company_name") || "") || null,
        name: String(f.get("name") || "") || null,
        email: String(f.get("email") || "") || null,
        phone: String(f.get("phone") || "") || null,
        phone_type: String(f.get("phone_type") || "unknown") as Supplier["phone_type"],
        call_consent: f.get("call_consent") === "on",
        timezone: String(f.get("timezone") || "") || null,
        fsc_tags: String(f.get("fsc_tags") || "")
          .split(/[,\s]+/)
          .map((x) => x.trim())
          .filter(Boolean),
        notes: String(f.get("notes") || "") || null,
      };
      if (initial) {
        return unwrap(await api.PATCH("/api/suppliers/{supplier_id}", { params: { path: { supplier_id: initial.id } }, body }));
      }
      return unwrap(await api.POST("/api/suppliers", { body: { ...body, cage_code: String(f.get("cage_code") || "").toUpperCase() } }));
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["suppliers"] });
      onDone();
    },
    onError: (e) => setErr(errorMessage(e)),
  });
  return (
    <form
      className="grid gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate(new FormData(e.currentTarget));
      }}
    >
      <div className="grid grid-cols-2 gap-3">
        <Field id="cage_code" label="CAGE code" defaultValue={initial?.cage_code} disabled={Boolean(initial)} required minLength={5} maxLength={5} className="uppercase" />
        <Field id="company_name" label="Company" defaultValue={initial?.company_name ?? ""} />
        <Field id="name" label="Contact name" defaultValue={initial?.name ?? ""} />
        <Field id="email" label="Quotes email" type="email" defaultValue={initial?.email ?? ""} />
        <Field id="phone" label="Phone" defaultValue={initial?.phone ?? ""} placeholder="+1…" />
        <div className="grid gap-1.5">
          <Label htmlFor="phone_type">Phone type</Label>
          <NativeSelect id="phone_type" name="phone_type" defaultValue={initial?.phone_type ?? "unknown"} className="h-9">
            <option value="landline">Landline</option>
            <option value="mobile">Mobile</option>
            <option value="unknown">Unknown</option>
          </NativeSelect>
        </div>
        <Field id="timezone" label="Timezone (optional)" placeholder="America/Chicago" defaultValue={initial?.timezone ?? ""} />
        <Field id="fsc_tags" label="Saved for FSCs" placeholder="1005, 2530" defaultValue={(initial?.fsc_tags ?? []).join(", ")} />
      </div>
      <Checkbox
        name="call_consent"
        label="This contact consented to AI-assisted calls (required for mobile numbers)"
        defaultChecked={initial?.call_consent ?? false}
      />
      <div className="grid gap-1.5">
        <Label htmlFor="notes">Notes</Label>
        <Textarea id="notes" name="notes" defaultValue={initial?.notes ?? ""} className="min-h-[60px]" />
      </div>
      <FormError message={err} />
      <div className="flex justify-end gap-2">
        <Button type="submit" disabled={save.isPending}>
          {initial ? "Save changes" : "Add supplier"}
        </Button>
      </div>
    </form>
  );
}

export default function SuppliersPage() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const readOnly = useReadOnly();
  const [editing, setEditing] = useState<Supplier | null>(null);
  const [adding, setAdding] = useState(false);
  const q = useQuery({
    queryKey: ["suppliers", search],
    queryFn: async () => unwrap(await api.GET("/api/suppliers", { params: { query: search ? { q: search } : {} } })),
  });
  const del = useMutation({
    mutationFn: async (id: number) => unwrap(await api.DELETE("/api/suppliers/{supplier_id}", { params: { path: { supplier_id: id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["suppliers"] }),
  });
  return (
    <div>
      <PageHeader
        title="Suppliers"
        description="Your firm's supplier directory. Contacts here are private to your organization and used for RFQ emails and calls."
        actions={
          <WriteGuard className="w-auto">
            <Button size="sm" onClick={() => setAdding(true)}>
              <Plus /> Add supplier
            </Button>
          </WriteGuard>
        }
      />
      <div className="relative mb-3 w-72">
        <Search className="pointer-events-none absolute left-2 top-2 h-4 w-4 text-muted-foreground" />
        <Input className="h-8 pl-7 text-xs" placeholder="Search CAGE, company, contact…" value={search} onChange={(e) => setSearch(e.target.value)} />
      </div>
      {q.isLoading ? (
        <Loading />
      ) : q.error ? (
        <ErrorState error={q.error} />
      ) : !q.data?.length && search ? (
        <Empty title={`No suppliers match “${search}”`}>Search looks at CAGE code, company, contact name and email.</Empty>
      ) : !q.data?.length ? (
        <Empty title="No suppliers yet">Add the distributors and manufacturers you buy from. Approved sources and past awardees show up on each RFQ automatically.</Empty>
      ) : (
        <div className="overflow-x-auto rounded-lg border bg-card shadow-sm">
          <Table>
            <THead>
              <tr>
                <TH>CAGE</TH>
                <TH>Company</TH>
                <TH>Location</TH>
                <TH>Contact</TH>
                <TH>Email</TH>
                <TH>Phone</TH>
                <TH>Calls</TH>
                <TH>FSCs</TH>
                <TH />
              </tr>
            </THead>
            <TBody>
              {q.data.map((s) => (
                <TR key={s.id}>
                  <TD className="font-mono text-[12px]">{s.cage_code}</TD>
                  <TD className="max-w-[220px] truncate">{s.company_name ?? "—"}</TD>
                  <TD className="text-[12px] text-muted-foreground">{[s.city, s.state].filter(Boolean).join(", ") || "—"}</TD>
                  <TD>{s.name ?? "—"}</TD>
                  <TD className="text-[12px]">{s.email ?? <span className="text-muted-foreground">missing</span>}</TD>
                  <TD className="text-[12px]">
                    {s.phone ? formatPhone(s.phone) : "—"}{" "}
                    {s.phone && s.phone_type !== "unknown" && <span className="text-muted-foreground">({s.phone_type})</span>}
                  </TD>
                  <TD>
                    {s.phone_type === "landline" || s.call_consent ? <Badge variant="success">Allowed</Badge> : <Badge variant="outline">No</Badge>}
                  </TD>
                  <TD className="text-[12px]">{s.fsc_tags.join(", ") || "—"}</TD>
                  <TD className="whitespace-nowrap text-right">
                    {!readOnly && (
                    <WriteGuard className="inline-flex w-auto">
                    <Button size="icon" variant="ghost" aria-label="Edit" onClick={() => setEditing(s)}>
                      <Pencil />
                    </Button>
                    <Button
                      size="icon"
                      variant="ghost"
                      aria-label="Delete"
                      onClick={() => {
                        if (confirm(`Delete supplier ${s.cage_code}?`)) del.mutate(s.id);
                      }}
                    >
                      <Trash2 />
                    </Button>
                    </WriteGuard>
                    )}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </div>
      )}
      <Dialog open={adding} onOpenChange={setAdding}>
        <DialogContent title="Add supplier" className="max-w-xl">
          <SupplierForm onDone={() => setAdding(false)} />
        </DialogContent>
      </Dialog>
      <Dialog open={Boolean(editing)} onOpenChange={(o) => !o && setEditing(null)}>
        {editing && (
          <DialogContent title={`Edit ${editing.cage_code}`} className="max-w-xl">
            <SupplierForm initial={editing} onDone={() => setEditing(null)} />
          </DialogContent>
        )}
      </Dialog>
    </div>
  );
}
