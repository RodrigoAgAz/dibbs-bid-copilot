import type { Metadata } from "next";
import { LegalPage } from "@/components/legal";

export const metadata: Metadata = { title: "Privacy Policy" };

export default function PrivacyPage() {
  return (
    <LegalPage title="Privacy Policy" updated="October 14, 2026">
      <section>
        <h2>What we collect</h2>
        <ul>
          <li>Account data: your name, work email, company name, CAGE code and a hashed password (we never store it in plain text).</li>
          <li>
            Business data you enter: watched supply classes, margin and cost settings, supplier contacts, quote requests, supplier replies
            and the bids you record.
          </li>
          <li>Operational logs: sign-in times and the technical logs needed to run and secure the Service.</li>
          <li>Public data: solicitations and awards published by DLA on DIBBS, which are not personal data about you.</li>
        </ul>
      </section>
      <section>
        <h2>How we use it</h2>
        <p>
          Only to provide the Service: pricing solicitations for your company, sending the quote requests you ask for, reading supplier
          replies sent to your quote-request address, and keeping your account secure. We do not sell your data or use it for advertising.
        </p>
      </section>
      <section>
        <h2>Isolation between customers</h2>
        <p>
          Each company&apos;s data is tied to its organization and every request is filtered to it at the database layer. Other customers
          never see your suppliers, quotes, settings or bids.
        </p>
      </section>
      <section>
        <h2>Service providers</h2>
        <p>
          Depending on the features enabled, we use a hosting provider for servers and databases, an email delivery provider to send quote
          requests, an AI provider to read supplier replies and polish drafts, and a telephony provider for optional supplier calls. They
          process data only to perform those services.
        </p>
      </section>
      <section>
        <h2>Cookies</h2>
        <p>
          We use only the cookies needed to keep you signed in and to protect forms against cross-site requests. No analytics or advertising
          cookies.
        </p>
      </section>
      <section>
        <h2>Retention and deletion</h2>
        <p>
          We keep your company&apos;s data while your account is active. You can ask us to export or delete it; deleted data is removed from
          active systems and from backups as they expire.
        </p>
      </section>
      <section>
        <h2>Security</h2>
        <p>
          Passwords are hashed with Argon2, sessions use short-lived tokens in httpOnly cookies, and traffic is encrypted in transit. No
          system is perfectly secure; tell us right away if you suspect unauthorized access to your account.
        </p>
      </section>
    </LegalPage>
  );
}
