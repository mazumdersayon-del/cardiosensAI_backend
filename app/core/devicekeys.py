"""Device keys and the signed message envelope (docs/device-protocol.md).

Every CardiosensAI device has a 32-byte factory secret K, written to its protected flash at manufacture and given
to the server once (admin CSV import). The server never keeps K. It keeps:
  - the MQTT login, as a Mosquitto PBKDF2-SHA512 hash of  hex(HMAC-SHA256(K, "cardiosens/mqtt/v1"));
  - the message-signing key  HMAC-SHA256(K, "cardiosens/sign/v1"), encrypted with AES-256-GCM under DEVICE_KEYS_SECRET.

Wire format of every MQTT message, both directions:  <64 hex HMAC-SHA256(sign_key, json)> "." <json bytes>.
The broker login proves which device is connected; the signature additionally proves each message came from the
device (or from the server) and wasn't changed, even if a broker account or the broker itself were compromised.
"""

import base64
import hashlib
import hmac
import json
import os
import re

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings

MQTT_LABEL = b"cardiosens/mqtt/v1"
SIGN_LABEL = b"cardiosens/sign/v1"
SECRET_RE = re.compile(r"^[0-9a-fA-F]{64}$")
# Device IDs are MQTT usernames and topic levels: no '/', '+', '#', spaces or control characters.
UID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{5,63}$")
MAX_MESSAGE_BYTES = 512 * 1024  # a 20 s 12-lead snapshot is ~320 KB as base64 JSON


def parse_secret(hex_secret: str) -> bytes:
    if not SECRET_RE.fullmatch(hex_secret or ""):
        raise ValueError("The factory secret must be 64 hex characters (32 random bytes)")
    k = bytes.fromhex(hex_secret)
    if len(set(k)) < 8:
        raise ValueError("The factory secret isn't random")
    return k


def mqtt_password(secret: bytes) -> str:
    return hmac.new(secret, MQTT_LABEL, hashlib.sha256).hexdigest()


def sign_key(secret: bytes) -> bytes:
    return hmac.new(secret, SIGN_LABEL, hashlib.sha256).digest()


def mosquitto_hash(password: str, *, salt: bytes | None = None, iterations: int = 101) -> str:
    """A Mosquitto 2.x password-file hash ($7$ = PBKDF2-SHA512), as `mosquitto_passwd` writes it."""
    salt = salt or os.urandom(12)
    dk = hashlib.pbkdf2_hmac("sha512", password.encode(), salt, iterations, dklen=64)
    return f"$7${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def mosquitto_check(password: str, stored: str) -> bool:
    try:
        _, kind, it, salt, h = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha512", password.encode(), base64.b64decode(salt), int(it), dklen=64)
    except (ValueError, TypeError):
        return False
    return kind == "7" and hmac.compare_digest(dk, base64.b64decode(h))


def _kek() -> AESGCM:
    return AESGCM(hashlib.sha256(b"cardiosens/device-kek/v1" + get_settings().device_keys_secret.encode()).digest())


def encrypt_key(key: bytes, device_uid: str) -> str:
    nonce = os.urandom(12)
    return base64.b64encode(nonce + _kek().encrypt(nonce, key, device_uid.encode())).decode()


def decrypt_key(blob: str, device_uid: str) -> bytes:
    raw = base64.b64decode(blob)
    return _kek().decrypt(raw[:12], raw[12:], device_uid.encode())


def seal(key: bytes, message: dict) -> bytes:
    body = json.dumps(message, separators=(",", ":")).encode()
    return hmac.new(key, body, hashlib.sha256).hexdigest().encode() + b"." + body


class BadMessage(ValueError):
    """The message isn't a validly signed envelope; `code` is safe to report back to the device."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def open_sealed(key: bytes, wire: bytes) -> dict:
    if len(wire) > MAX_MESSAGE_BYTES:
        raise BadMessage("too_large")
    sig, dot, body = wire.partition(b".")
    if not dot or len(sig) != 64:
        raise BadMessage("bad_envelope")
    expected = hmac.new(key, body, hashlib.sha256).hexdigest().encode()
    if not hmac.compare_digest(expected, sig.lower()):
        raise BadMessage("bad_signature")
    try:
        msg = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise BadMessage("bad_json") from exc
    if not isinstance(msg, dict):
        raise BadMessage("bad_json")
    return msg
