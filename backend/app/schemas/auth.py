from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class SignupIn(BaseModel):
    organization_name: str = Field(min_length=2, max_length=200)
    full_name: str | None = Field(default=None, max_length=200)
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    cage_code: str | None = Field(default=None, max_length=5)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class ForgotPasswordIn(BaseModel):
    email: EmailStr


class ResetPasswordIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(min_length=8, max_length=200)


class AcceptInviteIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    full_name: str | None = Field(default=None, max_length=200)
    password: str = Field(min_length=8, max_length=200)


class InviteIn(BaseModel):
    email: EmailStr
    role: str = Field(default="member", pattern="^(owner|member|viewer)$")


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    cage_code: str | None
    plan: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    full_name: str | None
    role: str
    is_superadmin: bool
    created_at: datetime
    last_login_at: datetime | None
    email_digest: bool = True


class PreferencesIn(BaseModel):
    email_digest: bool


class AuthOptionsOut(BaseModel):
    public_demo: bool


class MeOut(BaseModel):
    user: UserOut
    organization: OrganizationOut
    voice_globally_enabled: bool
    demo_mode: bool
    # False while EMAIL_PROVIDER=console: "Send" records quote requests but nothing reaches suppliers.
    email_delivery: bool = False


class InvitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    role: str
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None


class InviteCreatedOut(InvitationOut):
    # The link is returned so demo/console-email setups can share it directly.
    invite_url: str


class OkOut(BaseModel):
    ok: bool = True
    detail: str | None = None
