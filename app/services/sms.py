"""SMS delivery for one-time codes.

`console` is for development only: it logs that a code was sent (and the code itself
only when DEV_EXPOSE_OTP is on). In India, production SMS must go through a
DLT-registered sender and template.

`msg91` sends the code we generated through MSG91's SendOTP API. We pass our own code
(`otp=`), so verification stays on our side (hashed, attempt-limited); MSG91's own
verify endpoint is not used. The OTP template must be DLT-approved and contain ##OTP##.
"""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from app.core.config import get_settings
from app.core.errors import ApiError

log = logging.getLogger("cardiosensai.sms")


def mask_phone(phone: str) -> str:
    return phone[:3] + "•" * max(0, len(phone) - 7) + phone[-4:]


def sms_unavailable() -> ApiError:
    return ApiError(503, "sms_unavailable", "We couldn't send the code right now. Try again in a minute.")


def _send_msg91(phone: str, code: str) -> None:
    s = get_settings()
    query = urllib.parse.urlencode(
        {
            "template_id": s.msg91_template_id,
            "mobile": phone.lstrip("+"),  # international format without "+", e.g. 919812345678
            "otp": code,
            "otp_length": len(code),
            "otp_expiry": max(1, s.otp_ttl_s // 60),
            "realTimeResponse": 1,
        }
    )
    req = urllib.request.Request(  # noqa: S310 - base URL comes from settings, not from the request
        f"{s.msg91_base_url.rstrip('/')}/api/v5/otp?{query}",
        data=b"{}",
        method="POST",
        headers={"authkey": s.msg91_auth_key, "content-type": "application/json", "accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=s.sms_timeout_s) as resp:  # noqa: S310
            body = json.loads(resp.read() or b"{}")
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        log.error("otp_send_failed", extra={"extra_fields": {"to": mask_phone(phone), "provider": "msg91", "error": type(exc).__name__}})
        raise sms_unavailable() from exc
    # MSG91 answers 200 for most failures too; the outcome is in "type".
    if not isinstance(body, dict) or body.get("type") != "success":
        message = str(body.get("message", "unknown") if isinstance(body, dict) else "unexpected response")[:200]
        log.error("otp_send_failed", extra={"extra_fields": {"to": mask_phone(phone), "provider": "msg91", "error": message}})
        raise sms_unavailable()
    log.info("otp_sent", extra={"extra_fields": {"to": mask_phone(phone), "provider": "msg91", "request_id": body.get("request_id")}})


def send_otp(phone: str, code: str) -> None:
    s = get_settings()
    if s.sms_provider == "console":
        log.info("otp_sent", extra={"extra_fields": {"to": mask_phone(phone), **({"code": code} if s.dev_expose_otp else {})}})
        return
    if s.sms_provider == "msg91":
        _send_msg91(phone, code)
        return
    raise RuntimeError("No SMS provider configured")
