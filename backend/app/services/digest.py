"""Daily opportunity digest: one email per user each business morning, after the overnight DIBBS postings.

Content per organization: the most profitable bid-worthy lines that arrived in the last day, the bid-worthy lines
closing within two days that have no recorded bid yet, and supplier quotes waiting for review. Nothing new means
no email. Users opt out in Settings (users.email_digest). Demo and internal organizations never get digests.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.timeutil import today_et
from app.models import (
    IngestCheckpoint,
    Organization,
    QuoteRequest,
    Recommendation,
    Solicitation,
    SolicitationLine,
    SupplierQuote,
    User,
    UserRole,
)
from app.services.email.providers import EmailProvider, OutboundEmail, get_email_provider
from app.services.recommendations import org_settings

log = logging.getLogger(__name__)

TOP_N = 8
CLOSING_DAYS = 2
NO_DIGEST_PLANS = ("demo", "internal")


@dataclass
class DigestLine:
    solicitation_number: str
    nomenclature: str
    nsn: str
    quantity: float | None
    unit_of_issue: str | None
    return_by_date: date | None
    recommended_bid: float | None
    expected_profit: float | None
    p_win: float | None


@dataclass
class Digest:
    org_name: str
    new_bid_lines: int = 0
    new_needs_quote: int = 0
    new_expected_profit: float = 0.0
    top: list[DigestLine] = field(default_factory=list)
    closing: list[DigestLine] = field(default_factory=list)
    closing_total: int = 0
    needs_review: int = 0

    @property
    def empty(self) -> bool:
        return not (self.new_bid_lines or self.closing_total or self.needs_review)


def _line(rec: Recommendation, ln: SolicitationLine, sol: Solicitation) -> DigestLine:
    return DigestLine(sol.solicitation_number, ln.nomenclature or "", ln.nsn, ln.quantity, ln.unit_of_issue,
                      sol.return_by_date, rec.recommended_bid, rec.expected_profit, rec.p_win_at_recommended)


def build_digest(db: Session, org_id: int, now: datetime | None = None) -> Digest:
    now = now or datetime.now(UTC)
    today = today_et()
    org = db.get(Organization, org_id)
    fscs = list(org_settings(db, org_id).fsc_watchlist or [])
    d = Digest(org_name=org.name if org else "")
    rows = db.execute(
        select(Recommendation, SolicitationLine, Solicitation)
        .join(SolicitationLine, Recommendation.solicitation_line_id == SolicitationLine.id)
        .join(Solicitation, SolicitationLine.solicitation_id == Solicitation.id)
        .where(
            Recommendation.organization_id == org_id,
            Recommendation.no_bid.is_(False),
            Solicitation.return_by_date >= today,
            SolicitationLine.fsc.in_(fscs),
        )
    ).all()
    fresh = [r for r in rows if r[2].ingested_at and r[2].ingested_at >= now - timedelta(hours=26)]
    new = [r for r in fresh if r[0].expected_profit is not None]  # priced; "quote first" lines are counted apart
    d.new_needs_quote = len(fresh) - len(new)
    d.new_bid_lines = len(new)
    d.new_expected_profit = round(sum(r[0].expected_profit or 0 for r in new), 2)
    d.top = [_line(*r) for r in sorted(new, key=lambda r: -(r[0].expected_profit or 0))[:TOP_N]]
    closing = [r for r in rows
               if r[0].actual_bid is None and (r[2].return_by_date - today).days <= CLOSING_DAYS]
    d.closing_total = len(closing)
    d.closing = [_line(*r) for r in sorted(closing, key=lambda r: -(r[0].expected_profit or 0))[:5]]
    d.needs_review = db.execute(
        select(func.count()).select_from(SupplierQuote)
        .join(QuoteRequest, SupplierQuote.quote_request_id == QuoteRequest.id)
        .where(QuoteRequest.organization_id == org_id, SupplierQuote.needs_review.is_(True),
               SupplierQuote.reviewed_at.is_(None))
    ).scalar_one()
    return d


def _money(v: float | None) -> str:
    if v is None:
        return "-"
    return f"${v:,.2f}" if abs(v) >= 1 else f"${v:.4f}"


def render_digest(d: Digest, today: date) -> tuple[str, str]:
    base = get_settings().frontend_origin.rstrip("/")
    subject = (f"{d.new_bid_lines} new DIBBS lines worth bidding, est. profit {_money(d.new_expected_profit)}"
               if d.new_bid_lines else f"{d.closing_total} bid-worthy DIBBS lines close within {CLOSING_DAYS} days")
    out = [f"Good morning. Here is today's DIBBS summary for {d.org_name} ({today:%A, %B %d}).", ""]
    if d.top:
        out += [f"NEW SINCE YESTERDAY: {d.new_bid_lines} bid-worthy lines, est. profit {_money(d.new_expected_profit)}", ""]
        for x in d.top:
            out += [
                f"* {x.solicitation_number}  {x.nomenclature[:40]}  (NSN {x.nsn}, qty {x.quantity:g} {x.unit_of_issue or ''})"
                if x.quantity is not None else f"* {x.solicitation_number}  {x.nomenclature[:40]}  (NSN {x.nsn})",
                f"  Bid {_money(x.recommended_bid)}/unit, P(win) {round((x.p_win or 0) * 100)}%, "
                f"est. profit {_money(x.expected_profit)}, due {x.return_by_date:%b %d}",
                f"  {base}/rfq/{x.solicitation_number}",
            ]
        out.append("")
    if d.closing:
        out += [f"CLOSING WITHIN {CLOSING_DAYS} DAYS, NO BID RECORDED: {d.closing_total}", ""]
        for x in d.closing:
            out.append(f"* {x.solicitation_number}  due {x.return_by_date:%b %d}  est. profit {_money(x.expected_profit)}"
                       f"  {base}/rfq/{x.solicitation_number}")
        out.append("")
    if d.new_needs_quote:
        out += [f"{d.new_needs_quote} more new line(s) have no price history yet: request supplier quotes to price them "
                f"({base}/dashboard).", ""]
    if d.needs_review:
        out += [f"{d.needs_review} supplier quote(s) need your review: {base}/dashboard", ""]
    out += [f"All open lines: {base}/dashboard", "",
            "Recommendations only: review each solicitation and submit your bids on DIBBS.",
            f"To stop these emails, turn off the daily digest in Settings: {base}/settings"]
    return subject, "\n".join(out)


def send_daily_digests(db: Session, provider: EmailProvider | None = None, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    today = today_et()
    if provider is None:
        provider = get_email_provider()
        if provider.name == "console":
            # No real email provider configured: digests would only pile up in the outbox table.
            log.info("daily digest skipped: no email provider configured (EMAIL_PROVIDER=console)")
            return {"skipped": "console email provider", "sent": 0}
    sent = skipped = 0
    for org in db.execute(select(Organization).where(Organization.plan.not_in(NO_DIGEST_PLANS))).scalars():
        users = db.execute(
            select(User).where(User.organization_id == org.id, User.is_active.is_(True), User.email_digest.is_(True),
                               User.role.in_((UserRole.owner, UserRole.member)))
        ).scalars().all()
        if not users:
            continue
        d = build_digest(db, org.id, now)
        if d.empty:
            skipped += 1
            continue
        subject, body = render_digest(d, today)
        for u in users:
            key = f"{u.id}:{today.isoformat()}"
            done = db.execute(
                select(IngestCheckpoint.id).where(IngestCheckpoint.source == "digest", IngestCheckpoint.key == key)
            ).first()
            if done:
                continue
            res = provider.send(db, OutboundEmail(to=u.email, subject=subject, body=body, organization_id=org.id,
                                                  kind="digest"))
            if res.ok:
                db.add(IngestCheckpoint(source="digest", key=key, status="done"))
                sent += 1
            else:
                log.warning("digest to user %s failed: %s", u.id, res.error)
        db.commit()
    return {"sent": sent, "orgs_without_news": skipped}
