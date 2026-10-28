from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from environment variables (or a local .env in development)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: Literal["development", "test", "staging", "production"] = "development"
    app_name: str = "CardiosensAI API"
    api_base_url: str = "http://localhost:8000"

    database_url: str = "postgresql+psycopg://cardiosens:cardiosens@localhost:5432/cardiosens"

    # Secrets: must be long random values outside development (see validator).
    jwt_secret: str = "dev-only-insecure-jwt-secret-change-me-please-0000"  # noqa: S105 - refused outside dev by validator
    signing_key: str = "dev-only-insecure-signing-key-change-me-please-000"
    jwt_issuer: str = "cardiosensai"
    access_token_ttl_s: int = 15 * 60
    refresh_token_ttl_s: int = 30 * 24 * 3600
    mfa_token_ttl_s: int = 5 * 60

    otp_ttl_s: int = 10 * 60
    otp_max_attempts: int = 5
    # Development convenience: return the OTP in the API response. Refused outside development/test.
    dev_expose_otp: bool = False
    # One-time codes by SMS (sign-up needs a mobile number, sign-in can use it). India: DLT-registered sender + template.
    # Set up on the server with scripts/setup_msg91.sh.
    sms_provider: Literal["console", "none", "msg91"] = "console"
    msg91_auth_key: str = ""
    msg91_otp_template_id: str = ""  # DLT template with ##OTP##
    msg91_notice_template_id: str = ""  # optional: "this number already has an account" notice

    # Google sign-in (Google Identity Services). Web OAuth client ID; app client IDs are also accepted as audience.
    google_client_id: str = ""
    google_extra_client_ids: list[str] = Field(default_factory=list)

    # Email + password sign-in
    web_base_url: str = "http://localhost:5173"  # where links in emails point (the web app)
    email_provider: Literal["console", "smtp", "none"] = "console"
    email_from: str = "CardiosensAI <no-reply@cardiosens.ai>"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_tls: Literal["starttls", "ssl", "none"] = "starttls"
    password_reset_ttl_s: int = 3600
    login_max_failures: int = 5
    login_lockout_s: int = 15 * 60
    rate_limit_login_per_ip: int = 50
    refresh_cookie_name: str = "cs_refresh"

    # Medical documents (PDF / JPEG uploads)
    max_document_files: int = 10
    max_document_file_bytes: int = 15 * 1024 * 1024
    max_document_upload_bytes: int = 50 * 1024 * 1024
    document_link_ttl_s: int = 10 * 60

    invite_ttl_s: int = 72 * 3600
    report_link_ttl_s: int = 10 * 60

    storage_backend: Literal["local", "s3"] = "local"
    storage_local_path: str = "./var/storage"
    s3_bucket: str = ""
    s3_region: str = "ap-south-1"
    s3_endpoint_url: str | None = None

    cors_origins: list[str] = Field(default_factory=list)

    # Ingestion rules
    max_batch_size: int = 100
    future_skew_s: int = 5 * 60
    pre_registration_grace_s: int = 24 * 3600
    max_ecg_samples: int = 600_000  # 10 min at 1 kHz
    # Test streams (../Test_Scripts/stream_device.py): synthetic data from a registered device, stored labelled "simulated".
    # None = on in development/test, off in staging/production unless set to true.
    device_test_streams: bool | None = None

    rate_limit_otp_per_phone: int = 5
    rate_limit_otp_per_ip: int = 30
    rate_limit_window_s: int = 15 * 60

    @property
    def google_audiences(self) -> list[str]:
        return [a for a in (self.google_client_id, *self.google_extra_client_ids) if a]

    @property
    def test_streams_allowed(self) -> bool:
        return self.env in ("development", "test") if self.device_test_streams is None else self.device_test_streams

    @property
    def secure_cookies(self) -> bool:
        return self.env not in ("development", "test")

    @model_validator(mode="after")
    def _guard_production(self) -> "Settings":
        if self.env in ("staging", "production"):
            problems = []
            if "dev-only" in self.jwt_secret or len(self.jwt_secret) < 32:
                problems.append("JWT_SECRET must be a random value of at least 32 characters")
            if "dev-only" in self.signing_key or len(self.signing_key) < 32:
                problems.append("SIGNING_KEY must be a random value of at least 32 characters")
            if self.dev_expose_otp:
                problems.append("DEV_EXPOSE_OTP must be false")
            if self.sms_provider == "console":
                problems.append("SMS_PROVIDER must be a real provider (console only logs codes)")
            if self.sms_provider == "msg91" and not (self.msg91_auth_key and self.msg91_otp_template_id):
                problems.append("MSG91_AUTH_KEY and MSG91_OTP_TEMPLATE_ID are required when SMS_PROVIDER=msg91")
            if self.email_provider == "console":
                problems.append("EMAIL_PROVIDER must be smtp (console only logs messages, including password-reset links)")
            if self.email_provider == "smtp" and not self.smtp_host:
                problems.append("SMTP_HOST is required when EMAIL_PROVIDER=smtp")
            if problems:
                raise ValueError("Unsafe configuration: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
