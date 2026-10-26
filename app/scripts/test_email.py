"""Send one test email with the configured provider, to check SMTP settings.

    python -m app.scripts.test_email you@example.com

Uses the same settings as the API (EMAIL_PROVIDER, SMTP_*, EMAIL_FROM). Exit code 0 = sent.
`deploy.sh --test-email ADDRESS` runs it on the server as the service account.
"""

import argparse
import smtplib
import ssl
import sys

from app.core.config import get_settings
from app.services.email import send_email


def main() -> int:
    p = argparse.ArgumentParser(description="Send a test email with the configured provider")
    p.add_argument("to", help="recipient address")
    args = p.parse_args()
    s = get_settings()
    print(f"Provider: {s.email_provider}  From: {s.email_from}  Server: {s.smtp_host}:{s.smtp_port} ({s.smtp_tls})")
    if s.email_provider == "none":
        print("EMAIL_PROVIDER=none: nothing is sent. Add SMTP_* settings first.", file=sys.stderr)
        return 1
    if s.email_provider == "smtp":
        # Connect directly first so a failure shows the real reason (send_email only logs a type name).
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
        except (OSError, smtplib.SMTPException) as exc:
            print(f"Could not log in to {s.smtp_host}:{s.smtp_port}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
    ok = send_email(
        args.to,
        "CardiosensAI test email",
        "This is a test message from the CardiosensAI server. If you can read it, email delivery works.\n",
    )
    print("Sent." if ok else "Sending failed (see the log line above).")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
