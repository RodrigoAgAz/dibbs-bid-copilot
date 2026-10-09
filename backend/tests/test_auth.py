from __future__ import annotations

import re

from sqlalchemy import select

from app.models import EmailMessage, User, UserRole
from tests.conftest import csrf, make_client, signup


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    ready = client.get("/api/health/ready").json()
    assert ready["database"] is True


def test_signup_sets_httponly_cookies_and_me(client):
    r = client.post(
        "/api/auth/signup",
        json={"organization_name": "Acme Supply", "email": "Owner@Acme.example.com", "password": "s3cret-pass"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["user"]["email"] == "owner@acme.example.com"
    assert body["user"]["role"] == "owner"
    assert body["organization"]["name"] == "Acme Supply"
    set_cookie = ";".join(r.headers.get_list("set-cookie"))
    assert "access_token=" in set_cookie and "HttpOnly" in set_cookie
    assert "refresh_token=" in set_cookie
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "owner@acme.example.com"


def test_duplicate_signup_conflict(client, app):
    signup(client, "a@x.example.com")
    with make_client(app) as other:
        r = other.post(
            "/api/auth/signup", json={"organization_name": "Other", "email": "a@x.example.com", "password": "password-1"}
        )
        assert r.status_code == 409


def test_login_logout_and_bad_password(client, app):
    signup(client, "a@x.example.com", password="good-password")
    with make_client(app) as c2:
        assert c2.post("/api/auth/login", json={"email": "a@x.example.com", "password": "nope-nope"}).status_code == 401
        r = c2.post("/api/auth/login", json={"email": "a@x.example.com", "password": "good-password"})
        assert r.status_code == 200
        assert c2.get("/api/auth/me").status_code == 200
        assert c2.post("/api/auth/logout", headers=csrf(c2)).status_code == 200
        c2.cookies.clear()
        assert c2.get("/api/auth/me").status_code == 401


def test_unauthenticated_requests_rejected(client):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/suppliers").status_code == 401
    assert client.get("/api/settings").status_code == 401


def test_login_rate_limited(client, app):
    signup(client, "a@x.example.com", password="good-password")
    with make_client(app) as c2:
        codes = [
            c2.post("/api/auth/login", json={"email": "a@x.example.com", "password": "wrong-pass"}).status_code
            for _ in range(12)
        ]
        assert 429 in codes
        assert codes[0] == 401


def test_refresh_rotates_token(client):
    signup(client, "a@x.example.com")
    old_refresh = client.cookies.get("refresh_token")
    r = client.post("/api/auth/refresh", headers=csrf(client))
    assert r.status_code == 200
    new_refresh = client.cookies.get("refresh_token")
    assert new_refresh and new_refresh != old_refresh
    # The old refresh token is revoked.
    client.cookies.set("refresh_token", old_refresh, path="/api/auth")
    assert client.post("/api/auth/refresh", headers=csrf(client)).status_code == 401


def test_csrf_required_for_cookie_mutations(client):
    signup(client, "a@x.example.com")
    r = client.post("/api/suppliers", json={"cage_code": "1ABC2"})
    assert r.status_code == 403
    r = client.post("/api/suppliers", json={"cage_code": "1ABC2"}, headers={"x-csrf-token": "wrong"})
    assert r.status_code == 403
    r = client.post("/api/suppliers", json={"cage_code": "1ABC2"}, headers=csrf(client))
    assert r.status_code == 201


def test_password_reset_flow(client, app, db):
    signup(client, "a@x.example.com", password="old-password")
    with make_client(app) as anon:
        r = anon.post("/api/auth/forgot-password", json={"email": "a@x.example.com"})
        assert r.status_code == 200
        # Unknown emails get the same answer.
        assert anon.post("/api/auth/forgot-password", json={"email": "nobody@x.example.com"}).json() == r.json()
        msg = db.execute(select(EmailMessage).where(EmailMessage.to_addr == "a@x.example.com")).scalars().one()
        token = re.search(r"token=([\w\-]+)", msg.body).group(1)
        assert anon.post("/api/auth/reset-password", json={"token": token, "password": "new-password"}).status_code == 200
        # Single use.
        assert anon.post("/api/auth/reset-password", json={"token": token, "password": "another-pw"}).status_code == 400
        assert anon.post("/api/auth/login", json={"email": "a@x.example.com", "password": "old-password"}).status_code == 401
        assert anon.post("/api/auth/login", json={"email": "a@x.example.com", "password": "new-password"}).status_code == 200


def test_invite_member_flow(client, app, db):
    owner = signup(client, "owner@x.example.com", org="X Corp")
    r = client.post("/api/org/invitations", json={"email": "member@x.example.com"}, headers=csrf(client))
    assert r.status_code == 201
    token = r.json()["invite_url"].split("invite=")[1]
    with make_client(app) as invitee:
        r = invitee.post("/api/auth/accept-invite", json={"token": token, "password": "member-pass"})
        assert r.status_code == 200
        assert r.json()["organization"]["id"] == owner["organization"]["id"]
        assert r.json()["user"]["role"] == "member"
        # Members can't invite.
        r = invitee.post("/api/org/invitations", json={"email": "z@x.example.com"}, headers=csrf(invitee))
        assert r.status_code == 403
    members = client.get("/api/org/members").json()
    assert {m["email"] for m in members} == {"owner@x.example.com", "member@x.example.com"}
    u = db.execute(select(User).where(User.email == "member@x.example.com")).scalar_one()
    assert u.role == UserRole.member


def test_cannot_invite_email_registered_in_other_org(client, app):
    signup(client, "owner@x.example.com", org="Xco")
    with make_client(app) as other:
        signup(other, "taken@y.example.com", org="Yco")
    r = client.post("/api/org/invitations", json={"email": "taken@y.example.com"}, headers=csrf(client))
    assert r.status_code == 409
