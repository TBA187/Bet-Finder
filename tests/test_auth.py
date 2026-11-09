"""Accounts: sign-in, roles, requests, temporary passwords and protections."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.auth import AuthService, hash_password, temp_password, verify_password


@pytest.fixture
def app(paths):
    return create_app(paths, background=False)


def signed_in(app, login="admin", password="admin", ip="10.0.0.1") -> TestClient:
    c = TestClient(app, headers={"X-Forwarded-For": ip})
    r = c.post("/api/auth/login", json={"login": login, "password": password})
    assert r.status_code == 200, r.text
    return c


def make_user(admin: TestClient, username: str, role: str = "user", email: str = "") -> str:
    r = admin.post("/api/admin/users", json={"username": username, "email": email, "name": username.title(), "role": role})
    assert r.status_code == 200, r.text
    return r.json()["temp_password"]


def test_password_hashing():
    h = hash_password("correct horse")
    assert h.startswith("scrypt$") and "correct" not in h
    assert verify_password("correct horse", h) and not verify_password("wrong", h)
    assert not verify_password("x", "garbage")
    tp = temp_password()
    assert len(tp) == 14 and tp.count("-") == 2


def test_first_start_creates_admin_who_must_change_password(app):
    c = TestClient(app)
    assert c.get("/api/auth/me").status_code == 401
    assert c.get("/api/dashboard").status_code == 401
    me = signed_in(app).get("/api/auth/me").json()
    assert me["username"] == "admin" and me["role"] == "superadmin"
    assert me["must_change_password"] and me["default_password"] and "admin" in me["permissions"]
    assert "password_hash" not in me


def test_wrong_password_and_lockout(app):
    c = TestClient(app, headers={"X-Forwarded-For": "1.2.3.4"})
    for _ in range(5):
        r = c.post("/api/auth/login", json={"login": "admin", "password": "nope"})
        assert r.status_code == 401 and r.json()["code"] == "invalid"
    r = c.post("/api/auth/login", json={"login": "admin", "password": "admin"})
    assert r.status_code == 429 and r.json()["code"] == "locked"  # even the right password waits now
    signed_in(app, ip="5.6.7.8")  # another network is not blocked


def test_change_password_rules_and_other_devices(app):
    phone, laptop = signed_in(app, ip="1.1.1.1"), signed_in(app, ip="2.2.2.2")
    bad = [({"current_password": "wrong", "new_password": "Str0ng-pass!"}, "password_wrong"),
           ({"current_password": "admin", "new_password": "short"}, "password_short"),
           ({"current_password": "admin", "new_password": "password"}, "password_weak"),
           ({"current_password": "admin", "new_password": "admin"}, "password_same")]
    for body, code in bad:
        r = laptop.post("/api/account/password", json=body)
        assert r.status_code == 400 and r.json()["code"] == code
    r = laptop.post("/api/account/password", json={"current_password": "admin", "new_password": "Str0ng-pass!"})
    assert r.status_code == 200 and not r.json()["must_change_password"] and not r.json()["default_password"]
    assert laptop.get("/api/auth/me").status_code == 200   # this browser stays signed in
    assert phone.get("/api/auth/me").status_code == 401    # other devices are signed out
    assert TestClient(app).post("/api/auth/login", json={"login": "admin", "password": "admin"}).status_code == 401
    signed_in(app, password="Str0ng-pass!")


def test_roles_limit_what_people_can_do(app):
    admin = signed_in(app)
    user = signed_in(app, "viewer", make_user(admin, "viewer", "user"))
    editor = signed_in(app, "editor1", make_user(admin, "editor1", "editor"))
    for c in (user, editor, admin):
        assert c.get("/api/dashboard").status_code == 200
        assert c.get("/api/scanner").status_code == 200
        assert c.get("/api/tracker").status_code == 200
    # regular user: read-only + own tracker
    assert user.post("/api/refresh").status_code == 403
    assert user.get("/api/usage").status_code == 403
    assert user.get("/api/settings").status_code == 403
    assert user.put("/api/settings", json={"min_ev_pct": 3}).status_code == 403
    assert user.post("/api/manual/odds", json={}).status_code == 403
    assert user.get("/api/admin/users").status_code == 403
    # editor: data work, no settings/keys/accounts
    assert editor.get("/api/usage").status_code == 200
    assert editor.post("/api/manual/odds", json={}).status_code == 422  # allowed, body invalid
    assert editor.put("/api/settings", json={"min_ev_pct": 3}).status_code == 403
    assert editor.put("/api/competitions/football.epl", json={"enabled": False}).status_code == 403
    assert editor.get("/api/admin/requests").status_code == 403
    # everyone can change their own language and profile
    assert user.put("/api/account", json={"language": "da", "name": "Viewer Person"}).json()["language"] == "da"


def test_account_request_approval_and_temporary_password(app):
    guest = TestClient(app, headers={"X-Forwarded-For": "9.9.9.9"})
    body = {"name": "Jens Hansen", "username": "Jens.H", "email": "jens@example.com", "message": "Friend of admin"}
    assert guest.post("/api/auth/request-account", json=body).status_code == 200
    again = guest.post("/api/auth/request-account", json=body)
    assert again.status_code == 400 and again.json()["code"] == "request_pending"
    taken = guest.post("/api/auth/request-account", json={**body, "username": "admin", "email": "x@example.com"})
    assert taken.json()["code"] == "username_taken"
    # robots that fill the hidden field get a fake "ok" and nothing is stored
    assert guest.post("/api/auth/request-account", json={**body, "username": "bot", "website": "x"}).status_code == 200

    admin = signed_in(app)
    assert admin.get("/api/auth/me").json()["pending_requests"] == 1
    reqs = admin.get("/api/admin/requests").json()
    assert [r["username"] for r in reqs] == ["jens.h"]
    r = admin.post(f"/api/admin/requests/{reqs[0]['id']}/approve", json={"role": "user"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["email"]["sent"] is False and out["email"]["error"] == "not_configured"  # admin passes it on
    assert out["user"]["must_change_password"] and out["expires_at"]
    assert admin.post(f"/api/admin/requests/{reqs[0]['id']}/approve", json={}).json()["code"] == "request_handled"

    jens = signed_in(app, "jens@example.com", out["temp_password"])  # email works as login too
    me = jens.get("/api/auth/me").json()
    assert me["must_change_password"] and me["role"] == "user"
    r = jens.post("/api/account/password", json={"current_password": out["temp_password"], "new_password": "Jens-own-Pw-77"})
    assert r.status_code == 200 and not r.json()["must_change_password"]


def test_rejected_request_and_request_limit(app):
    admin = signed_in(app)
    guest = TestClient(app, headers={"X-Forwarded-For": "7.7.7.7"})
    for i in range(3):
        r = guest.post("/api/auth/request-account", json={"name": f"P {i}", "username": f"person{i}", "email": f"p{i}@example.com"})
        assert r.status_code == 200
    r = guest.post("/api/auth/request-account", json={"name": "P", "username": "person9", "email": "p9@example.com"})
    assert r.status_code == 429 and r.json()["code"] == "request_limit"
    rid = admin.get("/api/admin/requests").json()[0]["id"]
    r = admin.post(f"/api/admin/requests/{rid}/reject", json={"reason": "Unknown person"})
    assert r.status_code == 200 and r.json()["request"]["status"] == "rejected"
    assert len(admin.get("/api/admin/requests").json()) == 2


def test_forgot_password_creates_request_only_for_real_accounts(app):
    admin = signed_in(app)
    make_user(admin, "lise", email="lise@example.com")
    guest = TestClient(app, headers={"X-Forwarded-For": "8.8.8.8"})
    assert guest.post("/api/auth/forgot-password", json={"login": "nobody"}).status_code == 200
    assert guest.post("/api/auth/forgot-password", json={"login": "LISE@example.com"}).status_code == 200
    reqs = admin.get("/api/admin/requests").json()
    assert [(r["kind"], r["username"]) for r in reqs] == [("password_reset", "lise")]
    out = admin.post(f"/api/admin/requests/{reqs[0]['id']}/approve", json={}).json()
    signed_in(app, "lise", out["temp_password"])


def test_temporary_password_expires(app, paths):
    admin = signed_in(app)
    pw = make_user(admin, "late")
    auth: AuthService = app.state.container.auth
    with auth.store.edit("users", {}) as users:
        for u in users.values():
            if u["username"] == "late":
                u["temp_password_expires_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    r = TestClient(app).post("/api/auth/login", json={"login": "late", "password": pw})
    assert r.status_code == 403 and r.json()["code"] == "temp_expired"
    uid = next(u["id"] for u in admin.get("/api/admin/users").json() if u["username"] == "late")
    fresh = admin.post(f"/api/admin/users/{uid}/reset-password").json()["temp_password"]
    signed_in(app, "late", fresh)


def test_admin_protections_and_disabling(app):
    admin = signed_in(app)
    me = admin.get("/api/auth/me").json()
    assert admin.delete(f"/api/admin/users/{me['id']}").json()["code"] == "self_delete"
    assert admin.put(f"/api/admin/users/{me['id']}", json={"role": "user"}).json()["code"] == "last_admin"
    pw = make_user(admin, "bob")
    bob = signed_in(app, "bob", pw)
    uid = next(u["id"] for u in admin.get("/api/admin/users").json() if u["username"] == "bob")
    assert admin.put(f"/api/admin/users/{uid}", json={"disabled": True}).status_code == 200
    assert bob.get("/api/auth/me").status_code == 401  # signed out at once
    r = TestClient(app).post("/api/auth/login", json={"login": "bob", "password": pw})
    assert r.status_code == 403 and r.json()["code"] == "disabled"
    assert admin.delete(f"/api/admin/users/{uid}").status_code == 200
    events = [e["event"] for e in admin.get("/api/admin/activity").json()]
    assert {"login", "user_created", "user_updated", "user_deleted", "login_disabled"} <= set(events)


def test_logout_and_cross_site_protection(app):
    admin = signed_in(app)
    evil = admin.post("/api/refresh", headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403 and evil.json()["code"] == "cross_site"
    assert admin.post("/api/auth/logout").status_code == 200
    assert admin.get("/api/auth/me").status_code == 401


def test_each_account_has_its_own_tracker(app):
    from app.core.models import utcnow
    admin = signed_in(app)
    admin.put("/api/settings", json={"scraper_mode": "off"})
    ko = (utcnow() + timedelta(hours=3)).isoformat()
    for book, prices in (("pinnacle", {"home": 2.0, "draw": 3.5, "away": 3.9}), ("nordicbet", {"home": 2.2, "draw": 3.3, "away": 3.6})):
        r = admin.post("/api/manual/odds", json={"sport": "football", "home": "Gamma", "away": "Delta", "kickoff": ko,
                                                  "bookmaker": book, "market": "h2h", "prices": prices})
    fid = r.json()["fixture"]["id"]
    user = signed_in(app, "punter", make_user(admin, "punter"))
    item = user.post("/api/tracker/selection", json={"fixture_id": fid, "market_id": "h2h", "selection": "home", "stake": 5}).json()
    assert user.get("/api/tracker").json()["summary"]["tracked"] == 1
    assert admin.get("/api/tracker").json()["summary"]["tracked"] == 0
    assert admin.delete(f"/api/tracker/{item['id']}").status_code == 404  # not theirs
    assert user.delete(f"/api/tracker/{item['id']}").status_code == 200


def test_language_and_theme_are_saved_per_account(app):
    admin = signed_in(app)
    assert admin.get("/api/auth/me").json()["theme"] == "dark"  # dark is the default
    r = admin.put("/api/account", json={"theme": "light", "language": "da"}).json()
    assert r["theme"] == "light" and r["language"] == "da"
    again = signed_in(app).get("/api/auth/me").json()  # a new sign-in remembers both
    assert again["theme"] == "light" and again["language"] == "da"
    assert admin.put("/api/account", json={"theme": "pink"}).status_code == 422
