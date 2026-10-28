"""Send one test code by SMS through MSG91, to check the SMS settings.

    python -m app.scripts.test_sms +919812345678

Uses the same settings as the API (SMS_PROVIDER, MSG91_*). The message is a normal sign-in code
text with a random code that unlocks nothing. Exit code 0 = MSG91 accepted it.
`scripts/setup_msg91.sh --test` runs it on the server as the service account.
"""

import argparse
import re
import sys

from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.security import new_otp
from app.services.sms import mask_phone, send_otp


def main() -> int:
    p = argparse.ArgumentParser(description="Send a test SMS code with the configured provider")
    p.add_argument("to", help="mobile number, E.164 (+91...)")
    args = p.parse_args()
    if not re.fullmatch(r"\+[1-9]\d{7,14}", args.to):
        print("Give the number in E.164, e.g. +919812345678", file=sys.stderr)
        return 2
    s = get_settings()
    print(f"Provider: {s.sms_provider}")
    if s.sms_provider == "none":
        print("SMS_PROVIDER=none: nothing is sent. Run scripts/setup_msg91.sh first.", file=sys.stderr)
        return 1
    try:
        send_otp(args.to, new_otp())
    except ApiError as exc:
        print(f"Not sent: {exc.message} (details in the log line above)", file=sys.stderr)
        return 1
    print(f"Accepted by {s.sms_provider} for {mask_phone(args.to)}. Check the phone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
