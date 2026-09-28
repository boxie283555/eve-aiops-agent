from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PROJECT_CONFIG_PATHS = (
    "/app/config/project.yml",
    "config/project.yml",
)
LEGACY_SETTINGS_PATHS = (
    "/app/config/aiops.yml",
    "config/aiops.yml",
)


@dataclass(frozen=True)
class Settings:
    collector_interval_seconds: int
    command_timeout_seconds: int
    snapshot_time: str
    compare_time: str
    max_parallel_devices: int
    snapshots_path: Path
    reports_path: Path
    device_username: str | None
    device_password: str | None
    device_secret: str | None
    smtp_host: str | None
    smtp_port: int
    smtp_username: str | None
    smtp_password: str | None
    smtp_use_tls: bool
    smtp_from: str
    smtp_to: list[str]
    send_email: bool
    severity_threshold: str


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _load_yaml(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_project_config(path: str | None = None) -> dict[str, Any]:
    paths = [path] if path else [os.getenv("AIOPS_PROJECT_CONFIG"), *PROJECT_CONFIG_PATHS]
    for candidate in paths:
        if candidate and Path(candidate).exists():
            return _load_yaml(candidate)
    return {}


def _first_env_or_config(env_name: str, raw_value: Any) -> str | None:
    env_value = os.getenv(env_name)
    if env_value:
        return env_value
    if raw_value is None:
        return None
    value = str(raw_value)
    return value if value else None


def _smtp_recipients(raw_value: Any) -> list[str]:
    env_value = os.getenv("SMTP_TO")
    if env_value:
        raw_value = env_value
    if isinstance(raw_value, str):
        return [item.strip() for item in raw_value.split(",") if item.strip()]
    if isinstance(raw_value, list):
        return [str(item).strip() for item in raw_value if str(item).strip()]
    return []


def load_settings(path: str = "/app/config/aiops.yml") -> Settings:
    raw = load_project_config()
    if not raw:
        for candidate in (path, *LEGACY_SETTINGS_PATHS):
            if Path(candidate).exists():
                raw = _load_yaml(candidate)
                break
        else:
            raw = {}
    collector = raw.get("collector", {})
    alerting = raw.get("alerting", {})
    paths = raw.get("paths", {})
    credentials = raw.get("credentials", {})
    device_credentials = credentials.get("device", {})
    smtp_credentials = credentials.get("smtp", {})

    return Settings(
        collector_interval_seconds=int(collector.get("interval_seconds", 60)),
        command_timeout_seconds=int(collector.get("command_timeout_seconds", 30)),
        snapshot_time=str(collector.get("snapshot_time", "02:00")),
        compare_time=str(collector.get("compare_time", "02:20")),
        max_parallel_devices=int(collector.get("max_parallel_devices", 4)),
        snapshots_path=Path(paths.get("snapshots", "/app/data/snapshots")),
        reports_path=Path(paths.get("reports", "/app/data/reports")),
        device_username=_first_env_or_config("AIOPS_DEVICE_USERNAME", device_credentials.get("username")),
        device_password=_first_env_or_config("AIOPS_DEVICE_PASSWORD", device_credentials.get("password")),
        device_secret=_first_env_or_config("AIOPS_DEVICE_SECRET", device_credentials.get("secret")),
        smtp_host=_first_env_or_config("SMTP_HOST", smtp_credentials.get("host")),
        smtp_port=int(os.getenv("SMTP_PORT") or smtp_credentials.get("port", 25)),
        smtp_username=_first_env_or_config("SMTP_USERNAME", smtp_credentials.get("username")),
        smtp_password=_first_env_or_config("SMTP_PASSWORD", smtp_credentials.get("password")),
        smtp_use_tls=_env_bool("SMTP_USE_TLS", bool(smtp_credentials.get("use_tls", False))),
        smtp_from=os.getenv("SMTP_FROM") or str(smtp_credentials.get("from", "aiops-agent@example.local")),
        smtp_to=_smtp_recipients(smtp_credentials.get("to", [])),
        send_email=bool(alerting.get("send_email", True)),
        severity_threshold=str(alerting.get("severity_threshold", "major")),
    )
