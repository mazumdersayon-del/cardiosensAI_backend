"""SMS delivery for one-time codes.

`console` is for development only: it logs that a code was sent (and the code itself
only when DEV_EXPOSE_OTP is on). `none` sends nothing, so sign-in codes can't be
delivered and the API answers 503. In India, production SMS must go through a
DLT-registered sender and template — plug a provider in here.
"""

import logging

from app.core.config import get_settings
from app.core.errors import ApiError

log = logging.getLogger("cardiosensai.sms")


def mask_phone(phone: str) -> str:
    return phone[:3] + "•" * max(0, len(phone) - 7) + phone[-4:]


def send_otp(phone: str, code: str) -> None:
    s = get_settings()
    if s.sms_provider == "console":
        log.info("otp_sent", extra={"extra_fields": {"to": mask_phone(phone), **({"code": code} if s.dev_expose_otp else {})}})
        return
    log.error("otp_send_failed", extra={"extra_fields": {"to": mask_phone(phone), "error": "no SMS provider configured"}})
    raise ApiError(503, "sms_unavailable", "Sign-in codes can't be sent right now. Contact CardiosensAI support.")
