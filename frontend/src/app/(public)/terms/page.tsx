import type { Metadata } from "next";
import { LegalPage } from "@/components/legal";

export const metadata: Metadata = { title: "Terms of Service" };

export default function TermsPage() {
  return (
    <LegalPage title="Terms of Service" updated="October 14, 2026">
      <section>
        <h2>1. The service</h2>
        <p>
          The Invisible Hand (the &quot;Service&quot;) helps businesses that sell to the Defense Logistics Agency estimate winning prices for
          solicitations posted on DLA Internet Bid Board System (DIBBS), request quotes from their suppliers and decide what to bid. By
          creating an account or using the Service you agree to these terms on behalf of yourself and the company you represent.
        </p>
      </section>
      <section>
        <h2>2. Recommendations, not advice or guarantees</h2>
        <ul>
          <li>
            Predicted prices, win probabilities and recommended bids are statistical estimates based on public award data and the
            information you provide. They can be wrong. No outcome, award or profit is guaranteed.
          </li>
          <li>The Service never submits bids. You alone decide whether and what to bid, and you submit bids on DIBBS yourself.</li>
          <li>
            You are responsible for meeting every requirement of each solicitation and of applicable law and regulation (including the FAR,
            DFARS, export-control and small-business rules), and for the accuracy of your quotes and representations to the government.
          </li>
        </ul>
      </section>
      <section>
        <h2>3. Public data</h2>
        <p>
          The Service collects solicitation and award information that DLA publishes on DIBBS, at a limited request rate. It is not
          affiliated with or endorsed by DLA or the Department of Defense. Public data may be incomplete, delayed or corrected by its
          source; always confirm details in the solicitation itself.
        </p>
      </section>
      <section>
        <h2>4. Your account and your data</h2>
        <ul>
          <li>Keep your credentials confidential; you are responsible for activity under your account and your team&apos;s seats.</li>
          <li>
            Data you add (supplier contacts, quotes, settings, bids) belongs to your company. It is kept separate from other customers and
            used only to provide the Service to you.
          </li>
          <li>
            When you send quote requests or place supplier calls through the Service, you are responsible for having a legitimate business
            reason to contact those suppliers and for complying with applicable email, telephone and recording-consent laws.
          </li>
        </ul>
      </section>
      <section>
        <h2>5. Acceptable use</h2>
        <p>
          Do not use the Service to send unsolicited bulk email, to misrepresent yourself to suppliers or the government, to probe or
          overload the Service or DIBBS, or to resell the Service without permission.
        </p>
      </section>
      <section>
        <h2>6. Availability and changes</h2>
        <p>
          We work to keep the Service available and its data current but do not promise uninterrupted operation. We may change or
          discontinue features; we will give reasonable notice of material changes to these terms.
        </p>
      </section>
      <section>
        <h2>7. Limitation of liability</h2>
        <p>
          To the extent permitted by law, the Service is provided &quot;as is&quot;, and we are not liable for lost profits, lost awards or
          indirect or consequential damages arising from your use of it or from bids you decide to submit.
        </p>
      </section>
      <section>
        <h2>8. Ending your account</h2>
        <p>You can stop using the Service at any time and ask us to delete your company&apos;s data.</p>
      </section>
    </LegalPage>
  );
}
