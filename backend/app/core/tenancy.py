"""Multi-tenancy enforcement in ONE place.

When a request is authenticated, `bind_session_to_org(session, org_id)` marks the session.
SQLAlchemy event hooks then:
  * add `organization_id = :org` to every ORM SELECT/UPDATE/DELETE touching a TenantOwned model
    (via `with_loader_criteria`, which also propagates to relationship lazy loads), and
  * on flush, stamp new TenantOwned rows with the org id and refuse rows/changes for another org.

Endpoints therefore cannot read or write another organization's rows even if a handler forgets
to filter. `TenantContext` adds convenience helpers (404 on foreign ids) on top.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar

from fastapi import HTTPException, status
from sqlalchemy import event, select
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from app.models.base import TenantOwned
from app.models.tenancy import User

ORG_KEY = "tenant_org_id"
# Execution option for the rare deliberate global lookup (e.g. "is this email taken anywhere?").
# Grep for it: every use must be read-only and must not return foreign rows to the caller.
SKIP_TENANT_FILTER = "skip_tenant_filter"

T = TypeVar("T")


class TenantViolation(RuntimeError):
    pass


def bind_session_to_org(session: Session, org_id: int) -> None:
    session.info[ORG_KEY] = org_id


def unbind_session(session: Session) -> None:
    session.info.pop(ORG_KEY, None)


@event.listens_for(Session, "do_orm_execute")
def _add_tenant_criteria(state: ORMExecuteState) -> None:
    org_id = state.session.info.get(ORG_KEY)
    if org_id is None or state.execution_options.get(SKIP_TENANT_FILTER):
        return
    if (state.is_select and not state.is_column_load and not state.is_relationship_load) or (
        state.is_update or state.is_delete
    ):
        state.statement = state.statement.options(
            *(
                with_loader_criteria(cls, cls.organization_id == org_id, include_aliases=True)
                for cls in _tenant_classes()
            )
        )


_TENANT_CLASSES: list[type] | None = None


def _tenant_classes() -> list[type]:
    global _TENANT_CLASSES
    if _TENANT_CLASSES is None:
        from app.models import Base

        _TENANT_CLASSES = [m.class_ for m in Base.registry.mappers if issubclass(m.class_, TenantOwned)]
    return _TENANT_CLASSES


@event.listens_for(Session, "before_flush")
def _check_tenant_writes(session: Session, flush_context, instances) -> None:
    org_id = session.info.get(ORG_KEY)
    if org_id is None:
        return
    for obj in session.new:
        if isinstance(obj, TenantOwned):
            current = getattr(obj, "organization_id", None)
            if current is None:
                obj.organization_id = org_id
            elif current != org_id:
                raise TenantViolation(f"Refusing to create {type(obj).__name__} for another organization")
    for obj in list(session.dirty) + list(session.deleted):
        if isinstance(obj, TenantOwned) and getattr(obj, "organization_id", None) != org_id:
            raise TenantViolation(f"Refusing to modify {type(obj).__name__} of another organization")


@dataclass
class TenantContext:
    db: Session
    user: User

    @property
    def org_id(self) -> int:
        return self.user.organization_id

    def get(self, model: type[T], obj_id: int) -> T:
        """Fetch a tenant-owned row by id, 404 if missing or owned by another org."""
        obj = self.db.execute(
            select(model).where(model.id == obj_id, model.organization_id == self.org_id)  # type: ignore[attr-defined]
        ).scalar_one_or_none()
        if obj is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"{model.__name__} not found")
        return obj

    def add(self, obj: T) -> T:
        obj.organization_id = self.org_id  # type: ignore[attr-defined]
        self.db.add(obj)
        return obj
