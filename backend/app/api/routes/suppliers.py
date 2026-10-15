"""The organization's supplier directory (contacts are per organization)."""

from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import or_, select

from app.api.deps import get_tenant
from app.core.tenancy import TenantContext
from app.models import Company, PhoneType, SupplierContact
from app.schemas.auth import OkOut

router = APIRouter(prefix="/api/suppliers", tags=["suppliers"])


class SupplierIn(BaseModel):
    cage_code: str = Field(min_length=5, max_length=5)
    company_name: str | None = Field(default=None, max_length=300)
    name: str | None = Field(default=None, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=64)
    phone_type: PhoneType = PhoneType.unknown
    call_consent: bool = False
    timezone: str | None = Field(default=None, max_length=64)
    fsc_tags: list[str] = Field(default_factory=list, max_length=50)
    notes: str | None = Field(default=None, max_length=5000)


class SupplierPatch(BaseModel):
    company_name: str | None = Field(default=None, max_length=300)
    name: str | None = Field(default=None, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=64)
    phone_type: PhoneType | None = None
    call_consent: bool | None = None
    timezone: str | None = Field(default=None, max_length=64)
    fsc_tags: list[str] | None = None
    notes: str | None = Field(default=None, max_length=5000)


class SupplierOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    cage_code: str
    company_name: str | None
    name: str | None
    email: str | None
    phone: str | None
    phone_type: PhoneType
    call_consent: bool
    timezone: str | None
    fsc_tags: list[str]
    notes: str | None
    created_at: datetime
    updated_at: datetime
    # Public company data (CAGE registry), when known
    city: str | None = None
    state: str | None = None


def _clean_cage(c: str) -> str:
    c = c.strip().upper()
    if not re.fullmatch(r"[0-9A-Z]{5}", c):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "CAGE code must be 5 letters/digits")
    return c


def _out(ctx: TenantContext, s: SupplierContact) -> SupplierOut:
    out = SupplierOut.model_validate(s)
    comp = ctx.db.get(Company, s.cage_code)
    if comp:
        out.city, out.state = comp.city, comp.state
        if not out.company_name:
            out.company_name = comp.name
    return out


@router.get("", response_model=list[SupplierOut])
def list_suppliers(
    q: str | None = Query(default=None, max_length=100),
    fsc: str | None = Query(default=None, max_length=4),
    ctx: TenantContext = Depends(get_tenant),
) -> list[SupplierOut]:
    stmt = select(SupplierContact).order_by(SupplierContact.company_name, SupplierContact.cage_code)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                SupplierContact.cage_code.ilike(like),
                SupplierContact.company_name.ilike(like),
                SupplierContact.name.ilike(like),
                SupplierContact.email.ilike(like),
            )
        )
    rows = ctx.db.execute(stmt).scalars().all()
    if fsc:
        rows = [r for r in rows if fsc in (r.fsc_tags or [])]
    return [_out(ctx, r) for r in rows]


@router.post("", response_model=SupplierOut, status_code=status.HTTP_201_CREATED)
def create_supplier(body: SupplierIn, ctx: TenantContext = Depends(get_tenant)) -> SupplierOut:
    data = body.model_dump()
    data["cage_code"] = _clean_cage(data["cage_code"])
    data["email"] = str(data["email"]) if data["email"] else None
    if ctx.db.execute(select(SupplierContact.id).where(SupplierContact.cage_code == data["cage_code"])).first():
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"CAGE {data['cage_code']} is already in your supplier directory: edit that entry instead")
    row = ctx.add(SupplierContact(**data))
    ctx.db.commit()
    return _out(ctx, row)


@router.get("/{supplier_id}", response_model=SupplierOut)
def get_supplier(supplier_id: int, ctx: TenantContext = Depends(get_tenant)) -> SupplierOut:
    return _out(ctx, ctx.get(SupplierContact, supplier_id))


@router.patch("/{supplier_id}", response_model=SupplierOut)
def update_supplier(supplier_id: int, body: SupplierPatch, ctx: TenantContext = Depends(get_tenant)) -> SupplierOut:
    row = ctx.get(SupplierContact, supplier_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        if k == "email" and v is not None:
            v = str(v)
        setattr(row, k, v)
    ctx.db.commit()
    return _out(ctx, row)


@router.delete("/{supplier_id}", response_model=OkOut)
def delete_supplier(supplier_id: int, ctx: TenantContext = Depends(get_tenant)) -> OkOut:
    row = ctx.get(SupplierContact, supplier_id)
    ctx.db.delete(row)
    ctx.db.commit()
    return OkOut()
