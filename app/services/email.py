"""Outgoing email: one-time codes, password reset and account notices.

EMAIL_PROVIDER:
- `console` (development/test): logs the message and keeps it in `outbox` for tests.
- `smtp`: sends through any SMTP relay (Zoho, Google Workspace, Amazon SES SMTP, Brevo, ...).
- `none`: sends nothing and logs an error. Sign-up and password reset then can't complete.
"""

import logging
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

from app.core.config import get_settings

log = logging.getLogger("cardiosensai.email")


@dataclass
class SentEmail:
    to: str
    subject: str
    body: str


outbox: list[SentEmail] = []  # console provider only


def mask_email(address: str) -> str:
    local, _, domain = address.partition("@")
    return (local[:2] + "•" * max(1, len(local) - 2)) + "@" + domain


def send_email(to: str, subject: str, body: str) -> bool:
    s = get_settings()
    if s.email_provider == "console":
        outbox.append(SentEmail(to=to, subject=subject, body=body))
        log.info("email_sent", extra={"extra_fields": {"to": mask_email(to), "subject": subject, "body": body}})
        return True
    if s.email_provider == "smtp":
        msg = EmailMessage()
        msg["From"] = s.email_from
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        try:
            if s.smtp_tls == "ssl":
                server: smtplib.SMTP = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=15, context=ssl.create_default_context())
            else:
                server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15)
                if s.smtp_tls == "starttls":
                    server.starttls(context=ssl.create_default_context())
            with server:
                if s.smtp_username:
                    server.login(s.smtp_username, s.smtp_password)
                server.send_message(msg)
        except (OSError, smtplib.SMTPException) as exc:
            log.error("email_send_failed", extra={"extra_fields": {"to": mask_email(to), "error": type(exc).__name__}})
            return False
        # one-time codes are in the subject: never log them
        log.info("email_sent", extra={"extra_fields": {"to": mask_email(to), "subject": re.sub(r"\d{6}", "••••••", subject)}})
        return True
    log.error("email_send_failed", extra={"extra_fields": {"to": mask_email(to), "error": "no email provider configured"}})
    return False


def _link(path: str) -> str:
    return get_settings().web_base_url.rstrip("/") + path


def send_code(to: str, name: str | None, code: str, *, purpose: str) -> bool:
    """A one-time code: confirming the address at sign-up, signing in, or adding an email to an account."""
    what = {
        "signup": "finish creating your CardiosensAI account",
        "login": "sign in to CardiosensAI",
        "add_email": "add this email address to your CardiosensAI account",
    }.get(purpose, "continue")
    return send_email(
        to,
        f"{code} is your CardiosensAI code",
        f"Hello {name or ''},\n\nEnter this code to {what}:\n\n    {code}\n\nIt expires in 10 minutes and works once. "
        "Never share it: CardiosensAI staff will never ask for it. If you didn't ask for it, ignore this email.\n\nCardiosensAI",
    )


def send_password_reset(to: str, name: str | None, token: str, *, first_time: bool = False) -> bool:
    action = "set a password for" if first_time else "reset the password of"
    return send_email(
        to,
        "Set your CardiosensAI password" if first_time else "Reset your CardiosensAI password",
        f"Hello {name or ''},\n\nUse this link to {action} your CardiosensAI account:\n\n"
        f"{_link('/reset-password?token=' + token)}\n\nThe link works once and expires in 1 hour. "
        "If you didn't ask for this, ignore this email; your password stays the same.\n\nCardiosensAI",
    )


def send_registration_attempt(to: str) -> bool:
    return send_email(
        to,
        "Someone tried to register with your email",
        "Hello,\n\nSomeone tried to create a CardiosensAI account with this email address, but you already have one.\n\n"
        f"Sign in at {_link('/login')} or reset your password at {_link('/forgot-password')}.\n\n"
        "If this wasn't you, no action is needed.\n\nCardiosensAI",
    )


def send_verification_result(to: str, name: str | None, *, clinic: bool, decision: str, note: str | None) -> bool:
    """The outcome of CardiosensAI's check of a doctor's or clinic's registration (verified / rejected / revoked)."""
    what = "your clinic's registration" if clinic else "your medical registration"
    if decision == "verified":
        subject = "Your CardiosensAI account is verified"
        text = (
            f"CardiosensAI has verified {what}. Once two-step sign-in is on, you can link with patients "
            f"by their 16-digit ID and see the data they share with you.\n\nSign in at {_link('/')}"
        )
    else:
        subject = "Your CardiosensAI verification was revoked" if decision == "revoked" else "We couldn't verify your CardiosensAI account"
        lead = (
            f"CardiosensAI has withdrawn the verification of {what}. You can't see patient data until it is verified again."
            if decision == "revoked"
            else f"CardiosensAI couldn't verify {what}."
        )
        text = (
            f"{lead}\n\nReason: {note or 'not given'}\n\n"
            f"To fix it, sign in at {_link('/settings')}, correct your details or upload a clearer licence document, "
            "and submit again for review."
        )
    return send_email(to, subject, f"Hello {name or ''},\n\n{text}\n\nCardiosensAI")


def send_deletion_scheduled(to: str, name: str | None, erase_after: str) -> bool:
    return send_email(
        to,
        "Your CardiosensAI account will be deleted",
        f"Hello {name or ''},\n\nCardiosensAI staff have closed your account. You can no longer sign in, and nobody can see "
        f"your data any more. The account and its data will be erased permanently on {erase_after}.\n\n"
        "If you think this is a mistake, reply to this email or contact CardiosensAI support before that date.\n\nCardiosensAI",
    )


def send_account_restored(to: str, name: str | None) -> bool:
    return send_email(
        to,
        "Your CardiosensAI account is restored",
        f"Hello {name or ''},\n\nYour CardiosensAI account has been restored. You can sign in again at {_link('/')}. "
        "Links and shares that were active before are active again.\n\nCardiosensAI",
    )
