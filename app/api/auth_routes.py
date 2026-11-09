"""Sign-in, own account, and account administration (all under /api)."""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from app.services import mailer
from app.services.auth import CURRENT_USER, TEMP_PASSWORD_HOURS, AuthError, public_user

COOKIE = "bf_session"
ROLE = Literal["superadmin", "editor", "user"]


def client_ip(request: Request) -> str:
    """The visitor's address as seen by the nearest proxy.  Vercel replaces X-Forwarded-For
    with the real address; Caddy appends it, so the last entry is the one a visitor cannot fake."""
    fwd = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    if fwd:
        return fwd[-1][:64]
    return ((request.client.host if request.client else "") or "")[:64]


def is_https(request: Request) -> bool:
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"


def site_url(request: Request) -> str:
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{'https' if is_https(request) else 'http'}://{host}/"


# ------------------------------------------------------------------ models
class LoginIn(BaseModel):
    login: str = Field(..., min_length=1, max_length=120)
    password: str = Field(..., min_length=1, max_length=200)
    remember: bool = True


class AccountRequestIn(BaseModel):
    name: str = Field(..., max_length=80)
    username: str = Field(..., max_length=40)
    email: str = Field(..., max_length=120)
    message: str = Field("", max_length=500)
    language: Literal["en", "da"] = "en"
    website: str = Field("", max_length=200)  # hidden field: only robots fill it in


class ForgotIn(BaseModel):
    login: str = Field(..., min_length=1, max_length=120)
    language: Literal["en", "da"] = "en"
    website: str = Field("", max_length=200)


class ProfileIn(BaseModel):
    name: Optional[str] = Field(None, max_length=80)
    email: Optional[str] = Field(None, max_length=120)
    username: Optional[str] = Field(None, max_length=40)
    language: Optional[Literal["en", "da"]] = None
    theme: Optional[Literal["dark", "light"]] = None


class PasswordIn(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=200)
    new_password: str = Field(..., min_length=1, max_length=200)


class NewUserIn(BaseModel):
    name: str = Field("", max_length=80)
    username: str = Field(..., max_length=40)
    email: str = Field("", max_length=120)
    role: ROLE = "user"
    language: Literal["en", "da"] = "en"


class UserPatch(BaseModel):
    name: Optional[str] = Field(None, max_length=80)
    email: Optional[str] = Field(None, max_length=120)
    username: Optional[str] = Field(None, max_length=40)
    role: Optional[ROLE] = None
    disabled: Optional[bool] = None


class ApproveIn(BaseModel):
    role: ROLE = "user"


class RejectIn(BaseModel):
    reason: str = Field("", max_length=300)
    notify: bool = True


def build_auth_router(c) -> APIRouter:  # c: Container
    r = APIRouter()
    auth = c.auth

    def me() -> dict:
        user = CURRENT_USER.get()
        if not user:
            raise AuthError("auth_required", "Please sign in", 401)
        return user

    def me_payload(user: dict) -> dict:
        out = public_user(user)
        if "admin" in out["permissions"]:
            out["pending_requests"] = auth.pending_count()
            out["email_configured"] = mailer.configured()
        return out

    def deliver(request: Request, user: dict, password: str, kind: str) -> dict:
        """Email the temporary password when email is set up; the admin always sees it once."""
        subject, body = mailer.password_email(user.get("language", "en"), kind=kind, name=user.get("name", ""),
                                              username=user["username"], password=password, url=site_url(request),
                                              hours=TEMP_PASSWORD_HOURS)
        sent, error = mailer.send(user.get("email", ""), subject, body) if mailer.configured() else (False, "not_configured")
        return {"user": public_user(user), "temp_password": password, "expires_at": user.get("temp_password_expires_at"),
                "email": {"sent": sent, "error": error or None, "to": user.get("email", ""), "subject": subject, "body": body},
                "site_url": site_url(request)}

    # --------------------------------------------------------- public (no sign-in)
    @r.post("/auth/login")
    def login(body: LoginIn, request: Request, response: Response):
        user = auth.authenticate(body.login, body.password, client_ip(request))
        token, lifetime = auth.create_session(user, body.remember, client_ip(request), request.headers.get("user-agent", ""))
        response.set_cookie(COOKIE, token, max_age=lifetime if body.remember else None, httponly=True, samesite="lax",
                            secure=is_https(request), path="/")
        return me_payload(user)

    @r.post("/auth/logout")
    def logout(request: Request, response: Response):
        token = request.cookies.get(COOKIE)
        user = auth.session_user(token)
        auth.end_session(token)
        if user:
            auth.log("logout", actor=user, target=user["username"], ip=client_ip(request))
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @r.post("/auth/request-account")
    def request_account(body: AccountRequestIn, request: Request):
        if body.website:  # robot
            return {"ok": True}
        auth.request_account(name=body.name, username=body.username, email=body.email, message=body.message,
                             language=body.language, ip=client_ip(request))
        return {"ok": True}

    @r.post("/auth/forgot-password")
    def forgot(body: ForgotIn, request: Request):
        if not body.website:
            auth.request_password_reset(body.login, body.language, client_ip(request))
        return {"ok": True}

    # -------------------------------------------------------------- own account
    @r.get("/auth/me")
    def whoami():
        return me_payload(me())

    @r.put("/account")
    def update_profile(body: ProfileIn, request: Request):
        user = me()
        updated = auth.update_user(user["id"], body.model_dump(exclude_none=True), user, client_ip(request))
        return me_payload(updated)

    @r.post("/account/password")
    def change_password(body: PasswordIn, request: Request):
        user = me()
        updated = auth.change_password(user["id"], body.current_password, body.new_password, client_ip(request))
        auth.keep_session(request.cookies.get(COOKIE, ""), updated)
        return me_payload(updated)

    @r.post("/account/sign-out-others")
    def sign_out_others(request: Request):
        user = me()
        return me_payload(auth.sign_out_everywhere_else(user["id"], request.cookies.get(COOKIE, ""), client_ip(request)))

    # ---------------------------------------------------------- administration
    @r.get("/admin/users")
    def list_users():
        return auth.users()

    @r.post("/admin/users")
    def create_user(body: NewUserIn, request: Request):
        user, password = auth.create_user(username=body.username, email=body.email, name=body.name, role=body.role,
                                          actor=me(), language=body.language, ip=client_ip(request))
        return deliver(request, user, password, "account")

    @r.put("/admin/users/{user_id}")
    def update_user(user_id: str, body: UserPatch, request: Request):
        return public_user(auth.update_user(user_id, body.model_dump(exclude_none=True), me(), client_ip(request)))

    @r.post("/admin/users/{user_id}/reset-password")
    def reset_password(user_id: str, request: Request):
        user, password = auth.reset_password(user_id, me(), client_ip(request))
        return deliver(request, user, password, "password_reset")

    @r.delete("/admin/users/{user_id}")
    def delete_user(user_id: str, request: Request):
        auth.delete_user(user_id, me(), client_ip(request))
        return {"ok": True}

    @r.get("/admin/requests")
    def list_requests(status: Literal["pending", "approved", "rejected", "all"] = "pending"):
        return auth.requests(status)

    @r.post("/admin/requests/{rid}/approve")
    def approve(rid: str, body: ApproveIn, request: Request):
        req, user, password = auth.approve(rid, body.role, me(), client_ip(request))
        return {**deliver(request, user, password, req["kind"]), "request": req}

    @r.post("/admin/requests/{rid}/reject")
    def reject(rid: str, body: RejectIn, request: Request):
        req = auth.reject(rid, body.reason, me(), client_ip(request))
        email = {"sent": False, "error": None}
        if body.notify and req["kind"] == "account" and mailer.configured():
            subject, text = mailer.rejected_email(req.get("language", "en"), name=req["name"], reason=req.get("reason", ""))
            sent, error = mailer.send(req["email"], subject, text)
            email = {"sent": sent, "error": error or None}
        return {"request": req, "email": email}

    @r.get("/admin/activity")
    def activity(limit: int = 150):
        return auth.activity(max(1, min(limit, 300)))

    return r
