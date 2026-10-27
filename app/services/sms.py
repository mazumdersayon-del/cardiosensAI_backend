"""SMS delivery for one-time codes (sign-up, sign-in).

SMS_PROVIDER:
- `console` (development/test): logs that a code was sent (the code itself only when DEV_EXPOSE_OTP is on).
- `twilio`: Twilio Messages API (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM = number or MG... service SID).
- `msg91`: MSG91 OTP API with a DLT-registered template (MSG91_AUTH_KEY, MSG91_OTP_TEMPLATE_ID with ##OTP##;
  optional MSG91_NOTICE_TEMPLATE_ID for the "already registered" notice). In India, production SMS must use a
  DLT-registered sender and template.
- `none`: nothing can be sent; requests that need an SMS answer 503.
"""

import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from app.core.config import get_settings
from app.core.errors import ApiError

log = logging.getLogger("cardiosensai.sms")

UNAVAILABLE = ApiError(503, "sms_unavailable", "Text messages can't be sent right now. Try again later or contact CardiosensAI support.")


def mask_phone(phone: str) -> str:
    return phone[:3] + "•" * max(0, len(phone) - 7) + phone[-4:]


def available() -> bool:
    return get_settings().sms_provider != "none"


def _post(url: str, data: bytes, headers: dict[str, str]) -> None:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")  # noqa: S310 - fixed https URLs
    with urllib.request.urlopen(req, timeout=10) as res:  # noqa: S310
        if res.status >= 300:
            raise OSError(f"HTTP {res.status}")


def _twilio(phone: str, text: str) -> None:
    s = get_settings()
    sender = {"MessagingServiceSid": s.twilio_from} if s.twilio_from.startswith("MG") else {"From": s.twilio_from}
    auth = base64.b64encode(f"{s.twilio_account_sid}:{s.twilio_auth_token}".encode()).decode()
    _post(
        f"https://api.twilio.com/2010-04-01/Accounts/{urllib.parse.quote(s.twilio_account_sid)}/Messages.json",
        urllib.parse.urlencode({"To": phone, "Body": text, **sender}).encode(),
        {"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"},
    )


def _msg91(phone: str, template_id: str, variables: dict[str, str]) -> None:
    s = get_settings()
    headers = {"authkey": s.msg91_auth_key, "Content-Type": "application/json", "accept": "application/json"}
    mobile = phone.lstrip("+")
    if "otp" in variables:
        q = urllib.parse.urlencode({"template_id": template_id, "mobile": mobile, "otp": variables["otp"], "otp_expiry": 10})
        _post(f"https://control.msg91.com/api/v5/otp?{q}", b"{}", headers)
    else:
        body = {"template_id": template_id, "short_url": "0", "recipients": [{"mobiles": mobile, **variables}]}
        _post("https://control.msg91.com/api/v5/flow/", json.dumps(body).encode(), headers)


def _deliver(phone: str, kind: str, text: str, msg91_template: str, variables: dict[str, str], *, required: bool) -> None:
    s = get_settings()
    to = mask_phone(phone)
    if s.sms_provider == "console":
        log.info("sms_sent", extra={"extra_fields": {"to": to, "kind": kind, **({"text": text} if s.dev_expose_otp else {})}})
        return
    try:
        if s.sms_provider == "twilio":
            _twilio(phone, text)
        elif s.sms_provider == "msg91" and msg91_template:
            _msg91(phone, msg91_template, variables)
        elif s.sms_provider == "msg91":
            return  # no template for this message type: skip it
        else:
            raise OSError("no SMS provider configured")
    except (OSError, urllib.error.URLError, ValueError) as exc:
        log.error("sms_send_failed", extra={"extra_fields": {"to": to, "kind": kind, "error": type(exc).__name__}})
        if required:
            raise UNAVAILABLE from exc
        return
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
