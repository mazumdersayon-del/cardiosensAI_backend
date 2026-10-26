"""Outgoing email: address verification, password reset and link notices.

EMAIL_PROVIDER:
- `console` (development/test): logs the message and keeps it in `outbox` for tests.
- `smtp`: sends through any SMTP relay (Zoho, Google Workspace, Amazon SES SMTP, Brevo, ...).
- `none`: sends nothing and logs an error. Sign-up and password reset then can't complete.
"""

import logging
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
        log.info("email_sent", extra={"extra_fields": {"to": mask_email(to), "subject": subject}})
        return True
    log.error("email_send_failed", extra={"extra_fields": {"to": mask_email(to), "error": "no email provider configured"}})
    return False


def _link(path: str) -> str:
    return get_settings().web_base_url.rstrip("/") + path


def send_verification(to: str, name: str | None, token: str) -> bool:
    return send_email(
        to,
        "Confirm your CardiosensAI email address",
        f"Hello {name or ''},\n\nConfirm your email address to finish setting up your CardiosensAI account:\n\n"
        f"{_link('/verify-email?token=' + token)}\n\nThe link works once and expires in 24 hours. "
        "If you didn't create an account, ignore this email.\n\nCardiosensAI",
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
