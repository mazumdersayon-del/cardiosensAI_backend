"""16-digit CardiosensAI IDs that link patients, doctors and clinics.

Layout: 1 role digit + 14 random digits + 1 Luhn check digit.

    1xxx xxxx xxxx xxxC   patient
    2xxx xxxx xxxx xxxC   doctor
    3xxx xxxx xxxx xxxC   clinic (organisation)
    9xxx xxxx xxxx xxxC   CardiosensAI staff (admin)

Random, so IDs can't be guessed or used to count accounts. The check digit catches
single-digit typos and most adjacent swaps when an ID is typed in.
"""

import re
import secrets

ROLE_PREFIX = {"patient": "1", "doctor": "2", "clinic": "3", "admin": "9"}
PREFIX_ROLE = {v: k for k, v in ROLE_PREFIX.items()}
_DIGITS = re.compile(r"^\d{16}$")


def luhn_check_digit(body: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(body)):
        d = int(ch)
        if i % 2 == 0:  # these positions get doubled once the check digit is appended
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


def new_unique_id(role: str) -> str:
    body = ROLE_PREFIX[role] + f"{secrets.randbelow(10**14):014d}"
    return body + luhn_check_digit(body)


def normalize_unique_id(raw: str) -> str | None:
    """Strip spaces and dashes; return the ID if it is well formed with a valid check digit, else None."""
    uid = re.sub(r"[\s-]", "", raw or "")
    if not _DIGITS.match(uid) or uid[0] not in PREFIX_ROLE:
        return None
    return uid if luhn_check_digit(uid[:-1]) == uid[-1] else None


def role_of(uid: str) -> str:
    return PREFIX_ROLE[uid[0]]


def format_unique_id(uid: str) -> str:
    return " ".join(uid[i : i + 4] for i in range(0, 16, 4))
