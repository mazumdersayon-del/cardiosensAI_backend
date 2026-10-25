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

    otp_ttl_s: int = 5 * 60
    otp_max_attempts: int = 5
    # Development convenience: return the OTP in the API response. Refused outside development/test.
    dev_expose_otp: bool = False
    sms_provider: Literal["console", "none", "msg91"] = "console"
    # MSG91 SendOTP (https://docs.msg91.com/otp/sendotp). The template must be DLT-approved and contain ##OTP##.
    msg91_auth_key: str = ""
    msg91_template_id: str = ""
    msg91_base_url: str = "https://control.msg91.com"
    sms_timeout_s: float = 10.0

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

    rate_limit_otp_per_phone: int = 5
    rate_limit_otp_per_ip: int = 30
    rate_limit_window_s: int = 15 * 60

    @model_validator(mode="after")
    def _guard_production(self) -> "Settings":
        if self.sms_provider == "msg91" and not (self.msg91_auth_key and self.msg91_template_id):
            raise ValueError("SMS_PROVIDER=msg91 needs MSG91_AUTH_KEY and MSG91_TEMPLATE_ID")
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
            if problems:
                raise ValueError("Unsafe configuration: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
