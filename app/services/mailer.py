"""Optional email sending (temporary passwords, answers to account requests).

Works with any SMTP service.  Set these environment variables (Vercel ->
Settings -> Environment Variables) to switch it on:

    SMTP_HOST      e.g. smtp.gmail.com, smtp.resend.com, smtp-relay.brevo.com
    SMTP_PORT      587 (STARTTLS, default) or 465 (SSL)
    SMTP_USER      login name at the mail service
    SMTP_PASSWORD  password / app password / API key of the mail service
    SMTP_FROM      sender, e.g. "BetFinder <you@gmail.com>"

Without them BetFinder shows the temporary password to the admin, who can
copy it or open a ready-made email in their own mail program.
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import parseaddr

log = logging.getLogger("betfinder.mail")


def configured() -> bool:
    return bool(os.environ.get("SMTP_HOST") and (os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER")))


def send(to: str, subject: str, body: str) -> tuple[bool, str]:
    """Returns (sent, error).  Never raises; never logs the password."""
    if not to:
        return False, "no_email"
    if not configured():
        return False, "not_configured"
    host = os.environ["SMTP_HOST"].strip()
    port = int(os.environ.get("SMTP_PORT", "587") or 587)
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = (os.environ.get("SMTP_FROM") or user).strip()
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, sender, to
    msg.set_content(body)
    try:
        ctx = ssl.create_default_context()
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=15, context=ctx)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
            server.starttls(context=ctx)
        with server:
            if user:
                server.login(user, password)
            server.send_message(msg, from_addr=parseaddr(sender)[1] or sender)
        return True, ""
    except (smtplib.SMTPException, OSError) as exc:
        log.warning("Email to %s failed: %s", to, type(exc).__name__)
        return False, type(exc).__name__


TEXTS = {
    "en": {
        "subject_new": "Your BetFinder account is ready",
        "subject_reset": "Your new BetFinder password",
        "subject_rejected": "Your BetFinder account request",
        "body_password": ("Hi {name},\n\n{intro}\n\nSign in here: {url}\nUsername: {username}\n"
                          "Temporary password: {password}\n\nThe temporary password works for {hours} hours. "
                          "BetFinder will ask you to choose your own password after you sign in.\n\n- BetFinder"),
        "intro_new": "Your account request was approved.",
        "intro_reset": "Your password was reset.",
        "body_rejected": "Hi {name},\n\nYour request for a BetFinder account was not approved.{reason}\n\n- BetFinder",
        "reason": "\nReason: {reason}",
    },
    "da": {
        "subject_new": "Din BetFinder-konto er klar",
        "subject_reset": "Din nye BetFinder-adgangskode",
        "subject_rejected": "Din anmodning om en BetFinder-konto",
        "body_password": ("Hej {name}\n\n{intro}\n\nLog ind her: {url}\nBrugernavn: {username}\n"
                          "Midlertidig adgangskode: {password}\n\nDen midlertidige adgangskode virker i {hours} timer. "
                          "BetFinder beder dig vælge din egen adgangskode, når du er logget ind.\n\n- BetFinder"),
        "intro_new": "Din anmodning om en konto er godkendt.",
        "intro_reset": "Din adgangskode er nulstillet.",
        "body_rejected": "Hej {name}\n\nDin anmodning om en BetFinder-konto blev ikke godkendt.{reason}\n\n- BetFinder",
        "reason": "\nBegrundelse: {reason}",
    },
}


def password_email(lang: str, *, kind: str, name: str, username: str, password: str, url: str, hours: int) -> tuple[str, str]:
    t = TEXTS.get(lang, TEXTS["en"])
    subject = t["subject_reset" if kind == "password_reset" else "subject_new"]
    body = t["body_password"].format(name=name or username, intro=t["intro_reset" if kind == "password_reset" else "intro_new"],
                                     url=url, username=username, password=password, hours=hours)
    return subject, body


def rejected_email(lang: str, *, name: str, reason: str) -> tuple[str, str]:
    t = TEXTS.get(lang, TEXTS["en"])
    return t["subject_rejected"], t["body_rejected"].format(name=name, reason=t["reason"].format(reason=reason) if reason else "")
