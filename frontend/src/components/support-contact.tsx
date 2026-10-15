"use client";

import { useAuthOptions } from "@/lib/hooks";

/** Contact line for the legal pages (SUPPORT_EMAIL on the backend; hidden until configured). */
export function SupportContact() {
  const email = useAuthOptions().data?.support_email;
  if (!email) return null;
  return (
    <section data-testid="support-contact">
      <h2>Contact</h2>
      <p>
        Questions, data export or deletion requests:{" "}
        <a className="text-primary hover:underline" href={`mailto:${email}`}>
          {email}
        </a>
        .
      </p>
    </section>
  );
}
