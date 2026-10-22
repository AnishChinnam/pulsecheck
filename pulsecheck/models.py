from dataclasses import dataclass
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MonitorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(max_length=2048)
    expected_status: int = Field(default=200, ge=100, le=599)
    timeout_seconds: float = Field(default=2, ge=0.1, le=15, allow_inf_nan=False)
    attempts: int = Field(default=2, ge=1, le=3)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("Use a nonempty name without control characters")
        return value

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        value = value.strip()
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Use an absolute HTTP or HTTPS URL")
        if parsed.username or parsed.password or parsed.fragment:
            raise ValueError("Credentials and fragments are not supported in target URLs")
        try:
            parsed.port
        except ValueError as error:
            raise ValueError("Invalid port") from error
        if any(ord(c) < 33 for c in value):
            raise ValueError("URL contains whitespace or control characters")
        return value


class EnabledInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class OutageInput(BaseModel):
    down: bool


@dataclass(frozen=True)
class ProbeResult:
    monitor_id: int
    checked_at: str
    ok: bool
    latency_ms: float
    status_code: int | None
    attempts_used: int
    error: str | None = None
