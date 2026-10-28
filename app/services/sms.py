"""SMS delivery for one-time codes (sign-up, sign-in) through MSG91.

SMS_PROVIDER:
- `console` (development/test): logs that a code was sent (the code itself only when DEV_EXPOSE_OTP is on).
- `msg91`: MSG91 OTP API with a DLT-registered template (MSG91_AUTH_KEY, MSG91_OTP_TEMPLATE_ID with ##OTP##;
  optional MSG91_NOTICE_TEMPLATE_ID for the "already registered" notice, sent through the Flow API). In India,
  production SMS must use a DLT-registered sender and template. Set up with scripts/setup_msg91.sh.
- `none`: nothing can be sent; requests that need an SMS answer 503.

The code is made and checked here (services/otp.py stores only its HMAC); MSG91 only delivers it, so SMS and
email codes follow the same expiry, attempt and single-use rules.

When MSG91 fails, SMS counts as down for DOWN_FOR_S: `available()` is false, so sign-up carries on with the email code
only (the mobile number is confirmed later) and SMS sign-in points to email codes or the password, without waiting
on MSG91 for every request. The first successful send after that window clears it.
"""

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

from app.core.config import get_settings
from app.core.errors import ApiError

log = logging.getLogger("cardiosensai.sms")

UNAVAILABLE = ApiError(503, "sms_unavailable", "Text messages can't be sent right now. Try again later or contact CardiosensAI support.")
LOGIN_UNAVAILABLE = ApiError(
    503,
    "sms_unavailable",
    "We can't send text messages right now. Sign in with your email address instead (we'll email you a code), or use your password.",
)
SIGNUP_NEEDS_EMAIL = ApiError(
    503,
    "sms_unavailable",
    "We can't send text messages right now, so we can't confirm your mobile number. Add your email address to sign up now "
    "(you'll confirm the number later), or try again later.",
)
MSG91_API = "https://control.msg91.com/api/v5"
DOWN_FOR_S = 300
_down_until = 0.0  # monotonic time; in-process, like the rate limiter (one API process)


def mask_phone(phone: str) -> str:
    return phone[:3] + "•" * max(0, len(phone) - 7) + phone[-4:]


def available() -> bool:
    """A provider is set up and the last send didn't fail within DOWN_FOR_S."""
    return get_settings().sms_provider != "none" and time.monotonic() >= _down_until


def mark_down() -> None:
    global _down_until
    _down_until = time.monotonic() + DOWN_FOR_S


def reset() -> None:
    """Forget an outage (tests; a successful send does it too)."""
    global _down_until
    _down_until = 0.0


def _post(url: str, data: bytes, headers: dict[str, str]) -> dict:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")  # noqa: S310 - fixed https URL
    with urllib.request.urlopen(req, timeout=10) as res:  # noqa: S310
        if res.status >= 300:
            raise OSError(f"HTTP {res.status}")
        raw = res.read()
    try:
        return json.loads(raw or b"{}")
    except ValueError as exc:
        raise OSError("MSG91 answered with something that isn't JSON") from exc


def _msg91(phone: str, template_id: str, variables: dict[str, str]) -> None:
    s = get_settings()
    headers = {"authkey": s.msg91_auth_key, "Content-Type": "application/json", "accept": "application/json"}
    mobile = phone.lstrip("+")
    if "otp" in variables:
        q = urllib.parse.urlencode(
            {
                "template_id": template_id,
                "mobile": mobile,
                "otp": variables["otp"],
                "otp_length": len(variables["otp"]),
                "otp_expiry": max(1, s.otp_ttl_s // 60),  # minutes
            }
        )
        res = _post(f"{MSG91_API}/otp?{q}", b"{}", headers)
    else:
        body = {"template_id": template_id, "short_url": "0", "recipients": [{"mobiles": mobile, **variables}]}
        res = _post(f"{MSG91_API}/flow/", json.dumps(body).encode(), headers)
    # MSG91 reports most failures (bad auth key, unknown template, DLT mismatch) as HTTP 200 + {"type": "error"}.
    if res.get("type") != "success":
        raise OSError(f"MSG91: {str(res.get('message') or 'not accepted')[:120]}")


def _deliver(phone: str, kind: str, text: str, msg91_template: str, variables: dict[str, str], *, required: bool) -> None:
    s = get_settings()
    to = mask_phone(phone)
    if s.sms_provider == "console":
        log.info("sms_sent", extra={"extra_fields": {"to": to, "kind": kind, **({"text": text} if s.dev_expose_otp else {})}})
        return
    try:
        if s.sms_provider != "msg91":
            raise OSError("no SMS provider configured")
        if not msg91_template:
            return  # no template for this message type: skip it
        _msg91(phone, msg91_template, variables)
    except (OSError, urllib.error.URLError, ValueError) as exc:
        log.error("sms_send_failed", extra={"extra_fields": {"to": to, "kind": kind, "error": str(exc)[:160]}})
        mark_down()
        if required:
            raise UNAVAILABLE from exc
        return
    reset()
    log.info("sms_sent", extra={"extra_fields": {"to": to, "kind": kind}})


def send_otp(phone: str, code: str) -> None:
    """Send a one-time code. Raises 503 if it can't be sent (the user would wait for nothing)."""
    text = f"{code} is your CardiosensAI code. It expires in 10 minutes. Don't share it with anyone."
    _deliver(phone, "otp", text, get_settings().msg91_otp_template_id, {"otp": code}, required=True)


def send_already_registered(phone: str) -> None:
    """Sign-up used a number that already has an account: tell its owner instead of sending a code (best effort)."""
    text = (
        "Someone tried to create a CardiosensAI account with this number, which already has one. "
        "Sign in instead. If this wasn't you, ignore this message."
    )
    _deliver(phone, "already_registered", text, get_settings().msg91_notice_template_id, {}, required=False)
