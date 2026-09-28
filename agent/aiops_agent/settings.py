from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


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


def load_settings(path: str = "/app/config/aiops.yml") -> Settings:
    raw = _load_yaml(path)
    collector = raw.get("collector", {})
    alerting = raw.get("alerting", {})
    paths = raw.get("paths", {})

    smtp_to = [
        item.strip()
        for item in os.getenv("SMTP_TO", "").split(",")
        if item.strip()
    ]

    return Settings(
        collector_interval_seconds=int(collector.get("interval_seconds", 60)),
        command_timeout_seconds=int(collector.get("command_timeout_seconds", 30)),
        snapshot_time=str(collector.get("snapshot_time", "02:00")),
        compare_time=str(collector.get("compare_time", "02:20")),
        max_parallel_devices=int(collector.get("max_parallel_devices", 4)),
        snapshots_path=Path(paths.get("snapshots", "/app/data/snapshots")),
        reports_path=Path(paths.get("reports", "/app/data/reports")),
        device_username=os.getenv("AIOPS_DEVICE_USERNAME") or None,
        device_password=os.getenv("AIOPS_DEVICE_PASSWORD") or None,
        device_secret=os.getenv("AIOPS_DEVICE_SECRET") or None,
        smtp_host=os.getenv("SMTP_HOST") or None,
        smtp_port=int(os.getenv("SMTP_PORT", "25")),
        smtp_username=os.getenv("SMTP_USERNAME") or None,
        smtp_password=os.getenv("SMTP_PASSWORD") or None,
        smtp_use_tls=_env_bool("SMTP_USE_TLS"),
        smtp_from=os.getenv("SMTP_FROM", "aiops-agent@example.local"),
        smtp_to=smtp_to,
        send_email=bool(alerting.get("send_email", True)),
        severity_threshold=str(alerting.get("severity_threshold", "major")),
    )

