"""Appointment reminders (services/appointments.send_reminders): accepted appointments starting within the hour get one
reminder each, in the app, by email and (with MSG91_REMINDER_TEMPLATE_ID) by SMS. Also expires unanswered requests.

    python -m app.scripts.appointment_reminders

On the server `deploy.sh` runs this every 5 minutes (cardiosensai-reminders.timer). Safe to run any time.
"""

from app.core.db import SessionLocal, utcnow
from app.services.appointments import send_reminders


def main() -> int:
    with SessionLocal() as db:
        n = send_reminders(db, utcnow())
        db.commit()
    print(f"Sent {n} appointment reminder(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
