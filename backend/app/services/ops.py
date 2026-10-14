"""Operational health: is the data fresh, are the pipelines succeeding, is a model active?

`data_health()` backs the public GET /api/health/data endpoint (freshness only, no tenant data), the
superadmin overview, and the hourly `ops_check_job`, which logs one line per run and, when OPS_ALERT_EMAIL
is set, emails an alert (at most once per 12 hours) while something is stale or failing.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.timeutil import today_et
from app.models import (
    AwardHistory,
    IngestCheckpoint,
    IngestionRun,
    ModelRun,
    PricePrediction,
    Solicitation,
    SolicitationLine,
)

log = logging.getLogger(__name__)

# DIBBS posts one index file per business day; 4 days covers a weekend plus a federal holiday.
STALE_INDEX_DAYS = 4
# Ingest runs every 2 hours 06:00-20:00 ET; award labeling once a day.
STALE_RUN_HOURS = {"dibbs_rfq": 14, "dibbs_awards": 30}
ALERT_EVERY = timedelta(hours=12)
SYNTHETIC_PREFIX = "SPEZZ"  # demo solicitations (app/seed/demo.py)


def _last_run(db: Session, prefix: str, ok_only: bool) -> IngestionRun | None:
    stmt = select(IngestionRun).where(IngestionRun.source.like(f"{prefix}%"), IngestionRun.finished_at.is_not(None))
    if ok_only:
        stmt = stmt.where(IngestionRun.status.in_(("success", "partial")))
    return db.execute(stmt.order_by(IngestionRun.finished_at.desc()).limit(1)).scalar_one_or_none()


def _iso(dt: datetime | date | None) -> str | None:
    return dt.isoformat() if dt else None


def data_health(db: Session, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    today = today_et()
    s = get_settings()
    live = s.connector_mode != "fixture"

    index_keys = db.execute(
        select(IngestCheckpoint.key).where(IngestCheckpoint.source == "rfq_index", IngestCheckpoint.status == "done")
    ).scalars().all()
    index_dates = []
    for k in index_keys:
        try:
            index_dates.append(date.fromisoformat(k))
        except ValueError:
            continue
    newest_index = max(index_dates) if index_dates else None

    open_lines = db.execute(
        select(func.count()).select_from(SolicitationLine).join(Solicitation).where(
            Solicitation.return_by_date >= today, ~Solicitation.solicitation_number.startswith(SYNTHETIC_PREFIX)
        )
    ).scalar_one()
    real_awards = db.execute(
        select(func.count()).select_from(AwardHistory).where(AwardHistory.source != "synthetic")
    ).scalar_one()
    recent_awards = db.execute(
        select(func.count()).select_from(AwardHistory).where(
            AwardHistory.source != "synthetic", AwardHistory.award_date >= today - timedelta(days=30)
        )
    ).scalar_one()
    nsns = db.execute(
        select(func.count(func.distinct(AwardHistory.nsn))).where(AwardHistory.source != "synthetic")
    ).scalar_one()
    model = db.execute(select(ModelRun).where(ModelRun.is_active.is_(True)).limit(1)).scalar_one_or_none()

    runs = {}
    checks: list[dict] = []
    for key, label in (("dibbs_rfq", "RFQ ingest"), ("dibbs_awards", "Award labeling"), ("backfill", "Backfill")):
        ok = _last_run(db, key, ok_only=True)
        last = _last_run(db, key, ok_only=False)
        runs[key] = {
            "last_success_at": _iso(ok.finished_at if ok else None),
            "last_status": last.status if last else None,
            "last_finished_at": _iso(last.finished_at if last else None),
        }
        limit = STALE_RUN_HOURS.get(key)
        if live and limit is not None:
            fresh = ok is not None and now - ok.finished_at <= timedelta(hours=limit)
            checks.append({"name": f"{label} succeeded in the last {limit} h", "ok": fresh,
                           "detail": runs[key]["last_success_at"] or "never"})
        if live and last is not None and last.status == "failed":
            checks.append({"name": f"{label}: latest run failed", "ok": False, "detail": _iso(last.finished_at)})

    if live:
        index_ok = newest_index is not None and (today - newest_index).days <= STALE_INDEX_DAYS
        checks.append({"name": f"Newest DIBBS RFQ file is at most {STALE_INDEX_DAYS} days old", "ok": index_ok,
                       "detail": _iso(newest_index) or "none"})
        checks.append({"name": "Open RFQ lines in watched FSCs", "ok": open_lines > 0, "detail": str(open_lines)})
    checks.append({"name": "A pricing model is active", "ok": model is not None,
                   "detail": f"run {model.id}" if model else "none"})

    return {
        "status": "ok" if all(c["ok"] for c in checks) else "degraded",
        "checked_at": now.isoformat(),
        "connector_mode": s.connector_mode,
        "newest_rfq_posting_date": _iso(newest_index),
        "open_rfq_lines": int(open_lines),
        "award_rows": int(real_awards),
        "award_rows_last_30_days": int(recent_awards),
        "nsns_with_history": int(nsns),
        "model": _model_info(model, real_awards) if model else None,
        "live_track_record": live_track_record(db),
        "runs": runs,
        "checks": checks,
    }


def live_track_record(db: Session, since_days: int = 180) -> dict:
    """How the live predictions did once DLA posted the award (out of sample by construction).

    For real solicitations that closed and have since been awarded, take the last prediction made BEFORE the
    return-by date (end of day, ET-agnostic +1 day margin) and compare it with the winning unit price.
    """
    today = today_et()
    rows = db.execute(
        select(SolicitationLine.id, Solicitation.return_by_date, AwardHistory.unit_price)
        .join(Solicitation, SolicitationLine.solicitation_id == Solicitation.id)
        .join(AwardHistory, (AwardHistory.solicitation_number == Solicitation.solicitation_number)
              & (AwardHistory.nsn == SolicitationLine.nsn))
        .where(
            Solicitation.return_by_date < today,
            Solicitation.return_by_date >= today - timedelta(days=since_days),
            ~Solicitation.solicitation_number.startswith(SYNTHETIC_PREFIX),
            AwardHistory.source != "synthetic",
            AwardHistory.unit_price > 0,
        )
    ).all()
    winners: dict[int, tuple[date, float]] = {}
    for line_id, due, price in rows:
        winners.setdefault(line_id, (due, float(price)))
    inside = 0
    errors: list[float] = []
    ids = list(winners)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        preds: dict[int, list[PricePrediction]] = {}
        for p in db.execute(
            select(PricePrediction).where(PricePrediction.solicitation_line_id.in_(chunk))
            .order_by(PricePrediction.created_at)
        ).scalars():
            preds.setdefault(p.solicitation_line_id, []).append(p)
        for line_id in chunk:
            due, price = winners[line_id]
            cutoff = datetime.combine(due + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
            before = [p for p in preds.get(line_id, []) if p.created_at and p.created_at <= cutoff]
            if not before:
                continue
            q = before[-1].quantiles or {}
            levels, prices = list(q.get("levels") or []), list(q.get("prices") or [])
            try:
                lo, hi = prices[levels.index(0.1)], prices[levels.index(0.9)]
            except (ValueError, IndexError):
                continue
            med = float(q.get("median") or 0)
            if med <= 0:
                continue
            inside += int(lo <= price <= hi)
            errors.append(abs(med - price) / price)
    n = len(errors)
    errors.sort()
    return {
        "n": n,
        "coverage_80": round(inside / n, 4) if n else None,
        "median_abs_pct_error": round(errors[n // 2] if n % 2 else (errors[n // 2 - 1] + errors[n // 2]) / 2, 4)
        if n else None,
        "since_days": since_days,
    }


def _model_info(model: ModelRun, real_awards: int) -> dict:
    """Held-out accuracy of the active model: tested on the most recent awards it never saw (time split)."""
    from app.services.training import REAL_ONLY_THRESHOLD

    m = model.metrics or {}
    main, base = m.get("model") or {}, m.get("baseline") or {}
    test = (m.get("windows") or {}).get("test") or [None, None]
    return {
        "run_id": model.id,
        "trained_at": _iso(model.created_at),
        "pinball_mean": m.get("pinball_mean"),
        "test_rows": main.get("n"),
        "test_window": [str(test[0]) if test[0] else None, str(test[1]) if test[1] else None],
        "median_abs_pct_error": main.get("median_abs_pct_error"),
        "coverage_80": main.get("coverage_80"),
        "coverage_90": main.get("coverage_90"),
        "baseline_median_abs_pct_error": base.get("median_abs_pct_error"),
        # Training drops the synthetic demo history once this many real award rows exist.
        "real_data": real_awards >= REAL_ONLY_THRESHOLD,
    }


def run_ops_check(db: Session, now: datetime | None = None) -> dict:
    """Log the health summary; email OPS_ALERT_EMAIL (throttled) while degraded."""
    now = now or datetime.now(UTC)
    h = data_health(db, now)
    failing = [c for c in h["checks"] if not c["ok"]]
    line = (f"DATA HEALTH {h['status']}: newest index {h['newest_rfq_posting_date']}, "
            f"open lines {h['open_rfq_lines']}, awards {h['award_rows']} "
            f"({h['award_rows_last_30_days']} in 30 d), model {(h['model'] or {}).get('run_id')}")
    if not failing:
        log.info(line)
        return {**h, "alerted": False}
    log.warning("%s; failing: %s", line, "; ".join(f"{c['name']} ({c['detail']})" for c in failing))
    to = get_settings().ops_alert_email
    if not to:
        return {**h, "alerted": False}
    marker = db.execute(
        select(IngestCheckpoint).where(IngestCheckpoint.source == "ops_alert", IngestCheckpoint.key == "last")
    ).scalar_one_or_none()
    if marker is not None and marker.updated_at and now - marker.updated_at < ALERT_EVERY:
        return {**h, "alerted": False}
    from app.services.email.providers import OutboundEmail, get_email_provider

    body = "\n".join(
        ["DIBBS Bid Copilot data health is DEGRADED.", "", *[f"- {c['name']}: {c['detail']}" for c in failing], "",
         f"Newest RFQ posting date: {h['newest_rfq_posting_date']}",
         f"Open RFQ lines: {h['open_rfq_lines']}", f"Award rows: {h['award_rows']}",
         "", "Check the worker logs (railway logs -s worker) and /admin."]
    )
    res = get_email_provider().send(db, OutboundEmail(to=to, subject="[DIBBS Bid Copilot] data health degraded",
                                                      body=body, kind="ops_alert"))
    if marker is None:
        db.add(IngestCheckpoint(source="ops_alert", key="last", status="done", updated_at=now,
                                detail="sent" if res.ok else res.error))
    else:
        marker.detail = "sent" if res.ok else (res.error or "")[:200]
        marker.updated_at = now
    db.flush()
    return {**h, "alerted": res.ok}
