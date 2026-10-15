"""Which solicitation lines can still be bid on.

Open by date is not enough: DLA awards many automated (fast-award) solicitations before their return-by date.
Once a line's award has posted (an award row for its solicitation + NSN) or the record page says Awarded or
Cancelled, it is no longer an opportunity: it must leave the dashboard, the digest and re-pricing (re-pricing
an awarded line would also fold its own winning price into the "prediction").
"""

from __future__ import annotations

from sqlalchemy import and_, exists, or_

from app.models import AwardHistory, Solicitation, SolicitationLine

TERMINAL_STATUSES = ("Awarded", "Cancelled", "Canceled")


def not_awarded():
    """SQL condition over Solicitation + SolicitationLine (both must be in the FROM clause)."""
    return and_(
        or_(Solicitation.status.is_(None), Solicitation.status.not_in(TERMINAL_STATUSES)),
        ~exists().where(
            AwardHistory.solicitation_number == Solicitation.solicitation_number,
            AwardHistory.nsn == SolicitationLine.nsn,
        ),
    )
