"""Accounts, sign-in sessions, roles, account requests and a security log.

Everything is kept in the same storage as the rest of BetFinder (Upstash Redis
on Vercel, JSON files on your PC):

    users                  {user_id: account}
    sessions/<sha256>      one document per signed-in browser (expires)
    account_requests       {request_id: request}  (new accounts, forgotten passwords)
    auth_log               the latest security events (sign-ins, changes)
    auth_limits/<hash>     failed sign-in counters (stops password guessing)

Passwords are hashed with scrypt (salted, memory-hard).  Session ids are long
random strings; only their SHA-256 is stored, so a leaked database cannot be
used to sign in.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import uuid
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Optional

ROLES = ("superadmin", "editor", "user")
PERMISSIONS: dict[str, frozenset] = {
    "user": frozenset({"view", "track"}),
    "editor": frozenset({"view", "track", "refresh", "manual", "usage"}),
    "superadmin": frozenset({"view", "track", "refresh", "manual", "usage", "admin"}),
}
TEMP_PASSWORD_HOURS = 72
SESSION_DAYS = 30           # "keep me signed in"
SESSION_HOURS_SHORT = 12    # without "keep me signed in"
LOGIN_WINDOW = 15 * 60      # seconds
MAX_FAILS_PER_LOGIN = 5     # per IP + username
MAX_FAILS_PER_IP = 20
MAX_REQUESTS_PER_IP = 3     # account / password requests per hour
MAX_PENDING_REQUESTS = 100
LOG_SIZE = 300
DEFAULT_ADMIN = ("admin", "admin")

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
WEAK = {"password", "passw0rd", "12345678", "123456789", "1234567890", "qwertyui", "qwerty123", "adminadmin",
        "admin123", "administrator", "betfinder", "letmein1", "iloveyou", "11111111", "00000000", "abcdefgh"}
_TEMP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"

CURRENT_USER: ContextVar[Optional[dict]] = ContextVar("betfinder_user", default=None)


class AuthError(ValueError):
    """A problem the person can fix; ``code`` is translated by the web UI."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse(value: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


# ------------------------------------------------------------------ passwords
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    if hasattr(hashlib, "scrypt"):
        n, r, p = 2 ** 14, 8, 1
        dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
        return f"scrypt${n}${r}${p}${_b64(salt)}${_b64(dk)}"
    iterations = 600_000  # pragma: no cover - only without OpenSSL scrypt
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, *parts = stored.split("$")
        if scheme == "scrypt":
            n, r, p, salt, dk = parts
            expected = _unb64(dk)
            calc = hashlib.scrypt(password.encode("utf-8"), salt=_unb64(salt), n=int(n), r=int(r), p=int(p),
                                  dklen=len(expected))
        elif scheme == "pbkdf2_sha256":
            iterations, salt, dk = parts
            expected = _unb64(dk)
            calc = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), _unb64(salt), int(iterations))
        else:
            return False
        return hmac.compare_digest(calc, expected)
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = hash_password(secrets.token_hex(8))  # equal timing for unknown usernames


def temp_password() -> str:
    """Readable one-time password such as ``Kx7m-Pq4r-Zt9w``."""
    raw = "".join(secrets.choice(_TEMP_ALPHABET) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def check_password_rules(password: str, user: Optional[dict] = None) -> None:
    if len(password) < 8:
        raise AuthError("password_short", "The password must have at least 8 characters")
    if len(password) > 200:
        raise AuthError("password_long", "The password is too long")
    low = password.lower()
    names = {(user or {}).get("username", ""), ((user or {}).get("email") or "").split("@")[0]} - {""}
    if low in WEAK or low in names or len(set(password)) < 4:
        raise AuthError("password_weak", "This password is too easy to guess")


def norm_username(value: str) -> str:
    return (value or "").strip().lower()


def norm_email(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def check_username(value: str) -> str:
    u = norm_username(value)
    if not USERNAME_RE.match(u):
        raise AuthError("username_invalid", "Username: 3-32 characters, letters, numbers, dot, dash or underscore")
    return u


def check_email(value: Optional[str], required: bool = False) -> str:
    e = norm_email(value)
    if not e:
        if required:
            raise AuthError("email_invalid", "Enter a valid email address")
        return ""
    if len(e) > 120 or not EMAIL_RE.match(e):
        raise AuthError("email_invalid", "Enter a valid email address")
    return e


def check_name(value: Optional[str]) -> str:
    name = " ".join((value or "").split())
    if len(name) > 80:
        raise AuthError("name_long", "The name is too long")
    return name


def permissions(role: str) -> frozenset:
    return PERMISSIONS.get(role, PERMISSIONS["user"])


def public_user(u: dict) -> dict:
    expires = u.get("temp_password_expires_at")
    return {
        "id": u["id"], "username": u["username"], "email": u.get("email", ""), "name": u.get("name", ""),
        "role": u["role"], "language": u.get("language", "en"), "theme": u.get("theme", "dark"),
        "disabled": bool(u.get("disabled")),
        "must_change_password": bool(u.get("must_change_password")), "default_password": bool(u.get("default_password")),
        "temp_password_expires_at": expires, "created_at": u.get("created_at"), "created_by": u.get("created_by"),
        "last_login_at": u.get("last_login_at"), "password_changed_at": u.get("password_changed_at"),
        "permissions": sorted(permissions(u["role"])),
    }


class AuthService:
    def __init__(self, store):
        self.store = store

    # ------------------------------------------------------------- storage
    def _users(self, fresh: bool = False) -> dict:
        if fresh:
            self.store.forget("users")
        data = self.store.read("users", {})
        return data if isinstance(data, dict) else {}

    def _edit(self, name: str, default):
        self.store.forget(name)  # never edit a cached copy another server may have changed
        return self.store.edit(name, default)

    def log(self, event: str, *, actor: Optional[dict] = None, target: Optional[str] = None, ip: str = "",
            detail: str = "") -> None:
        entry = {"at": _iso(_now()), "event": event, "actor": (actor or {}).get("username"), "target": target,
                 "ip": ip, "detail": detail[:200]}
        with self._edit("auth_log", []) as data:
            if isinstance(data, list):
                data.append(entry)
                del data[:-LOG_SIZE]

    def activity(self, limit: int = 100) -> list[dict]:
        data = self.store.read("auth_log", [])
        return list(reversed(data[-limit:])) if isinstance(data, list) else []

    # ------------------------------------------------------------ accounts
    def ensure_admin(self) -> Optional[dict]:
        """First start: create the super admin  admin / admin  (must be changed)."""
        if self._users(fresh=True):
            return None
        username, password = DEFAULT_ADMIN
        now = _iso(_now())
        user = {"id": uuid.uuid4().hex[:12], "username": username, "email": "", "name": "Administrator",
                "role": "superadmin", "language": "en", "disabled": False, "password_hash": hash_password(password),
                "must_change_password": True, "default_password": True, "temp_password_expires_at": None,
                "created_at": now, "created_by": "system", "epoch": 1}
        with self._edit("users", {}) as data:
            if data:  # another server was faster
                return None
            data[user["id"]] = user
        self.log("admin_created", target=username, detail="default super admin")
        return user

    def users(self) -> list[dict]:
        order = {r: i for i, r in enumerate(ROLES)}
        return sorted((public_user(u) for u in self._users().values()),
                      key=lambda u: (order.get(u["role"], 9), u["username"]))

    def get(self, user_id: str) -> Optional[dict]:
        return self._users().get(user_id)

    def find(self, login: str, users: Optional[dict] = None) -> Optional[dict]:
        key = (login or "").strip().lower()
        if not key:
            return None
        for u in (users if users is not None else self._users()).values():
            if u["username"] == key or (u.get("email") and u["email"] == key):
                return u
        return None

    def _taken(self, users: dict, username: str = "", email: str = "", ignore: Optional[str] = None) -> None:
        for u in users.values():
            if u["id"] == ignore:
                continue
            if username and u["username"] == username:
                raise AuthError("username_taken", "This username is already taken")
            if email and u.get("email") == email:
                raise AuthError("email_taken", "This email address is already used by another account")

    def _active_admins(self, users: dict) -> list[dict]:
        return [u for u in users.values() if u["role"] == "superadmin" and not u.get("disabled")]

    def create_user(self, *, username: str, email: str, name: str, role: str, actor: Optional[dict],
                    language: str = "en", ip: str = "") -> tuple[dict, str]:
        if role not in ROLES:
            raise AuthError("role_invalid", "Unknown role")
        username, email, name = check_username(username), check_email(email), check_name(name)
        password = temp_password()
        now = _now()
        user = {"id": uuid.uuid4().hex[:12], "username": username, "email": email, "name": name, "role": role,
                "language": language if language in ("en", "da") else "en", "disabled": False,
                "password_hash": hash_password(password), "must_change_password": True, "default_password": False,
                "temp_password_expires_at": _iso(now + timedelta(hours=TEMP_PASSWORD_HOURS)),
                "created_at": _iso(now), "created_by": (actor or {}).get("username", "system"), "epoch": 1}
        with self._edit("users", {}) as data:
            self._taken(data, username, email)
            data[user["id"]] = user
        self.log("user_created", actor=actor, target=username, ip=ip, detail=role)
        return user, password

    def update_user(self, user_id: str, patch: dict, actor: dict, ip: str = "") -> dict:
        changes = []
        with self._edit("users", {}) as data:
            u = data.get(user_id)
            if not u:
                raise AuthError("not_found", "Account not found", 404)
            if "username" in patch and patch["username"] is not None:
                username = check_username(patch["username"])
                self._taken(data, username=username, ignore=user_id)
                if username != u["username"]:
                    changes.append(f"username {u['username']}->{username}")
                    u["username"] = username
            if "email" in patch and patch["email"] is not None:
                email = check_email(patch["email"])
                self._taken(data, email=email, ignore=user_id)
                if email != u.get("email", ""):
                    changes.append("email")
                    u["email"] = email
            if "name" in patch and patch["name"] is not None:
                u["name"] = check_name(patch["name"])
            if "language" in patch and patch["language"] in ("en", "da"):
                u["language"] = patch["language"]
            if "theme" in patch and patch["theme"] in ("dark", "light"):
                u["theme"] = patch["theme"]
            if "role" in patch and patch["role"] is not None and patch["role"] != u["role"]:
                if patch["role"] not in ROLES:
                    raise AuthError("role_invalid", "Unknown role")
                if u["role"] == "superadmin" and len(self._active_admins(data)) <= 1:
                    raise AuthError("last_admin", "There must always be at least one active super admin")
                changes.append(f"role {u['role']}->{patch['role']}")
                u["role"] = patch["role"]
            if "disabled" in patch and patch["disabled"] is not None and bool(patch["disabled"]) != bool(u.get("disabled")):
                if patch["disabled"]:
                    if user_id == actor["id"]:
                        raise AuthError("self_disable", "You cannot disable your own account")
                    if u["role"] == "superadmin" and len(self._active_admins(data)) <= 1:
                        raise AuthError("last_admin", "There must always be at least one active super admin")
                    u["epoch"] = int(u.get("epoch", 1)) + 1  # signs the account out everywhere
                changes.append("disabled" if patch["disabled"] else "enabled")
                u["disabled"] = bool(patch["disabled"])
            result = dict(u)
        if changes and actor["id"] != user_id:
            self.log("user_updated", actor=actor, target=result["username"], ip=ip, detail=", ".join(changes))
        elif changes:
            self.log("profile_updated", actor=actor, target=result["username"], ip=ip, detail=", ".join(changes))
        return result

    def delete_user(self, user_id: str, actor: dict, ip: str = "") -> None:
        if user_id == actor["id"]:
            raise AuthError("self_delete", "You cannot delete your own account")
        with self._edit("users", {}) as data:
            u = data.get(user_id)
            if not u:
                raise AuthError("not_found", "Account not found", 404)
            if u["role"] == "superadmin" and len(self._active_admins(data)) <= 1:
                raise AuthError("last_admin", "There must always be at least one active super admin")
            data.pop(user_id)
        self.log("user_deleted", actor=actor, target=u["username"], ip=ip)

    def reset_password(self, user_id: str, actor: Optional[dict], ip: str = "") -> tuple[dict, str]:
        password = temp_password()
        with self._edit("users", {}) as data:
            u = data.get(user_id)
            if not u:
                raise AuthError("not_found", "Account not found", 404)
            u["password_hash"] = hash_password(password)
            u["must_change_password"] = True
            u["default_password"] = False
            u["temp_password_expires_at"] = _iso(_now() + timedelta(hours=TEMP_PASSWORD_HOURS))
            u["epoch"] = int(u.get("epoch", 1)) + 1
            result = dict(u)
        self.log("password_reset", actor=actor, target=result["username"], ip=ip)
        return result, password

    def change_password(self, user_id: str, current: str, new: str, ip: str = "") -> dict:
        with self._edit("users", {}) as data:
            u = data.get(user_id)
            if not u:
                raise AuthError("not_found", "Account not found", 404)
            if not verify_password(current, u["password_hash"]):
                raise AuthError("password_wrong", "The current password is wrong")
            if current == new:
                raise AuthError("password_same", "Choose a password that is different from the current one")
            check_password_rules(new, u)
            u["password_hash"] = hash_password(new)
            u["must_change_password"] = False
            u["default_password"] = False
            u["temp_password_expires_at"] = None
            u["password_changed_at"] = _iso(_now())
            u["epoch"] = int(u.get("epoch", 1)) + 1  # other devices are signed out
            result = dict(u)
        self.log("password_changed", actor=result, target=result["username"], ip=ip)
        return result

    # -------------------------------------------------------------- sign-in
    def _limit_key(self, kind: str, value: str) -> str:
        return "auth_limits/" + hashlib.sha256(f"{kind}|{value}".encode()).hexdigest()[:40]

    def _count(self, key: str, window: int) -> int:
        doc = self.store.read(key, None)
        if not isinstance(doc, dict):
            return 0
        since = _parse(doc.get("since"))
        if not since or (_now() - since).total_seconds() > window:
            return 0
        return int(doc.get("count", 0))

    def _bump(self, key: str, window: int) -> None:
        self.store.forget(key)
        doc = self.store.read(key, None)
        since = _parse(doc.get("since")) if isinstance(doc, dict) else None
        if not since or (_now() - since).total_seconds() > window:
            doc = {"count": 0, "since": _iso(_now())}
        doc["count"] = int(doc.get("count", 0)) + 1
        self.store.write(key, doc, pretty=False, ttl=window)

    def authenticate(self, login: str, password: str, ip: str = "") -> dict:
        login_key = (login or "").strip().lower()
        k_login, k_ip = self._limit_key("login", f"{ip}|{login_key}"), self._limit_key("ip", ip)
        if self._count(k_login, LOGIN_WINDOW) >= MAX_FAILS_PER_LOGIN or self._count(k_ip, LOGIN_WINDOW) >= MAX_FAILS_PER_IP:
            self.log("login_blocked", target=login_key[:40], ip=ip)
            raise AuthError("locked", "Too many failed attempts. Wait 15 minutes and try again.", 429)
        users = self._users(fresh=True)
        user = self.find(login_key, users)
        ok = verify_password(password or "", user["password_hash"] if user else _DUMMY_HASH)
        if not user or not ok:
            self._bump(k_login, LOGIN_WINDOW)
            self._bump(k_ip, LOGIN_WINDOW)
            self.log("login_failed", target=login_key[:40], ip=ip)
            raise AuthError("invalid", "Wrong username/email or password", 401)
        if user.get("disabled"):
            self.log("login_disabled", target=user["username"], ip=ip)
            raise AuthError("disabled", "This account is disabled. Contact the administrator.", 403)
        expires = _parse(user.get("temp_password_expires_at"))
        if user.get("must_change_password") and expires and expires < _now():
            self.log("login_temp_expired", target=user["username"], ip=ip)
            raise AuthError("temp_expired", "Your temporary password has expired. Ask the administrator for a new one.", 403)
        self.store.delete(k_login)
        with self._edit("users", {}) as data:
            if user["id"] in data:
                data[user["id"]]["last_login_at"] = _iso(_now())
                user = dict(data[user["id"]])
        self.log("login", actor=user, target=user["username"], ip=ip)
        return user

    # -------------------------------------------------------------- sessions
    @staticmethod
    def _session_key(token: str) -> str:
        return "sessions/" + hashlib.sha256(token.encode()).hexdigest()

    def create_session(self, user: dict, remember: bool, ip: str = "", agent: str = "") -> tuple[str, int]:
        token = secrets.token_urlsafe(32)
        lifetime = SESSION_DAYS * 86400 if remember else SESSION_HOURS_SHORT * 3600
        now = _now()
        self.store.write(self._session_key(token), {
            "user_id": user["id"], "epoch": int(user.get("epoch", 1)), "created_at": _iso(now),
            "expires_at": _iso(now + timedelta(seconds=lifetime)), "remember": remember, "ip": ip, "agent": agent[:160],
        }, pretty=False, ttl=lifetime)
        if secrets.randbelow(20) == 0:  # files backend: remove old sessions now and then
            self.store.cleanup("sessions/", SESSION_DAYS * 86400 + 86400)
        return token, lifetime

    def session_user(self, token: Optional[str]) -> Optional[dict]:
        if not token or len(token) > 200:
            return None
        key = self._session_key(token)
        doc = self.store.read(key, None)
        if not isinstance(doc, dict):
            return None
        expires = _parse(doc.get("expires_at"))
        if not expires or expires < _now():
            self.store.delete(key)
            return None
        user = self._users().get(doc.get("user_id"))
        if not user or user.get("disabled") or int(doc.get("epoch", 0)) != int(user.get("epoch", 1)):
            return None
        return user

    def end_session(self, token: Optional[str]) -> None:
        if token and len(token) <= 200:
            self.store.delete(self._session_key(token))

    def keep_session(self, token: str, user: dict) -> None:
        """After a password change: this browser stays signed in, all others are signed out."""
        key = self._session_key(token)
        self.store.forget(key)
        doc = self.store.read(key, None)
        if isinstance(doc, dict):
            doc["epoch"] = int(user.get("epoch", 1))
            remaining = (_parse(doc["expires_at"]) - _now()).total_seconds()
            self.store.write(key, doc, pretty=False, ttl=max(60, int(remaining)))

    def sign_out_everywhere_else(self, user_id: str, token: str, ip: str = "") -> dict:
        with self._edit("users", {}) as data:
            u = data.get(user_id)
            if not u:
                raise AuthError("not_found", "Account not found", 404)
            u["epoch"] = int(u.get("epoch", 1)) + 1
            result = dict(u)
        self.keep_session(token, result)
        self.log("signed_out_others", actor=result, target=result["username"], ip=ip)
        return result

    # ------------------------------------------------------------- requests
    def _requests(self) -> dict:
        data = self.store.read("account_requests", {})
        return data if isinstance(data, dict) else {}

    def requests(self, status: Optional[str] = "pending") -> list[dict]:
        items = [r for r in self._requests().values() if status in (None, "all") or r["status"] == status]
        return sorted(items, key=lambda r: r["created_at"], reverse=True)

    def pending_count(self) -> int:
        return sum(1 for r in self._requests().values() if r["status"] == "pending")

    def _request_limit(self, ip: str) -> None:
        key = self._limit_key("request", ip)
        if self._count(key, 3600) >= MAX_REQUESTS_PER_IP:
            raise AuthError("request_limit", "Too many requests from this network. Try again in an hour.", 429)
        self._bump(key, 3600)

    def request_account(self, *, name: str, username: str, email: str, message: str, language: str,
                        ip: str = "") -> dict:
        name, username, email = check_name(name), check_username(username), check_email(email, required=True)
        if not name:
            raise AuthError("name_required", "Enter your name")
        message = (message or "").strip()[:500]
        self._request_limit(ip)
        users = self._users(fresh=True)
        self._taken(users, username, email)
        req = {"id": uuid.uuid4().hex[:12], "kind": "account", "status": "pending", "name": name, "username": username,
               "email": email, "message": message, "language": language if language in ("en", "da") else "en",
               "created_at": _iso(_now()), "ip": ip}
        with self._edit("account_requests", {}) as data:
            pending = [r for r in data.values() if r["status"] == "pending"]
            if len(pending) >= MAX_PENDING_REQUESTS:
                raise AuthError("request_limit", "Too many open requests. Try again later.", 429)
            for r in pending:
                if r["kind"] == "account" and (r["username"] == username or r["email"] == email):
                    raise AuthError("request_pending", "A request with this username or email is already waiting for approval")
            data[req["id"]] = req
            for rid in [r["id"] for r in sorted(data.values(), key=lambda r: r["created_at"])
                        if r["status"] != "pending"][:-200]:
                data.pop(rid, None)  # keep the latest 200 handled requests
        self.log("account_requested", target=username, ip=ip)
        return req

    def request_password_reset(self, login: str, language: str, ip: str = "") -> None:
        """Always looks successful, so nobody can find out which accounts exist."""
        self._request_limit(ip)
        user = self.find(login)
        if not user or user.get("disabled"):
            self.log("reset_requested_unknown", target=(login or "")[:40], ip=ip)
            return
        with self._edit("account_requests", {}) as data:
            if any(r["status"] == "pending" and r["kind"] == "password_reset" and r.get("user_id") == user["id"]
                   for r in data.values()):
                return
            rid = uuid.uuid4().hex[:12]
            data[rid] = {"id": rid, "kind": "password_reset", "status": "pending", "name": user.get("name", ""),
                         "username": user["username"], "email": user.get("email", ""), "user_id": user["id"],
                         "message": "", "language": language if language in ("en", "da") else user.get("language", "en"),
                         "created_at": _iso(_now()), "ip": ip}
        self.log("reset_requested", target=user["username"], ip=ip)

    def _close_request(self, rid: str, status: str, actor: dict, **extra) -> dict:
        with self._edit("account_requests", {}) as data:
            req = data.get(rid)
            if not req:
                raise AuthError("not_found", "Request not found", 404)
            if req["status"] != "pending":
                raise AuthError("request_handled", "This request was already handled")
            req.update(status=status, handled_by=actor["username"], handled_at=_iso(_now()), **extra)
            return dict(req)

    def pending_request(self, rid: str) -> dict:
        req = self._requests().get(rid)
        if not req:
            raise AuthError("not_found", "Request not found", 404)
        if req["status"] != "pending":
            raise AuthError("request_handled", "This request was already handled")
        return req

    def approve(self, rid: str, role: str, actor: dict, ip: str = "") -> tuple[dict, dict, str]:
        req = self.pending_request(rid)
        if req["kind"] == "password_reset":
            user, password = self.reset_password(req["user_id"], actor, ip)
        else:
            user, password = self.create_user(username=req["username"], email=req["email"], name=req["name"], role=role,
                                               actor=actor, language=req.get("language", "en"), ip=ip)
        req = self._close_request(rid, "approved", actor, user_id=user["id"])
        self.log("request_approved", actor=actor, target=req["username"], ip=ip, detail=req["kind"])
        return req, user, password

    def reject(self, rid: str, reason: str, actor: dict, ip: str = "") -> dict:
        req = self._close_request(rid, "rejected", actor, reason=(reason or "").strip()[:300])
        self.log("request_rejected", actor=actor, target=req["username"], ip=ip, detail=req["kind"])
        return req
