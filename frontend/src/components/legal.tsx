/** Shared layout for the Terms and Privacy pages. The texts are the operator's starting point and should be
 * reviewed by counsel before relying on them commercially (see DECISIONS.md). */
export function LegalPage({ title, updated, children }: { title: string; updated: string; children: React.ReactNode }) {
  return (
    <article className="mx-auto max-w-3xl px-4 py-12">
      <h1 className="text-2xl font-semibold">{title}</h1>
      <p className="mt-1 text-xs text-muted-foreground">Last updated {updated}</p>
      <div className="mt-8 space-y-6 text-sm leading-6 text-foreground/90 [&_h2]:mb-2 [&_h2]:text-base [&_h2]:font-semibold [&_li]:ml-5 [&_li]:list-disc [&_ul]:space-y-1">
        {children}
      </div>
    </article>
  );
}
