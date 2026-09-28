from __future__ import annotations

import difflib
import json
from datetime import datetime, timezone
from pathlib import Path

from .collector import Collector, DeviceResult
from .emailer import send_report
from .settings import Settings


def write_snapshot(settings: Settings, results: dict[str, DeviceResult]) -> Path:
    now = datetime.now(timezone.utc)
    snapshot_dir = settings.snapshots_path / now.strftime("%Y-%m-%d")
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "created_at": now.isoformat(),
        "devices": {},
    }

    for name, result in sorted(results.items()):
        device_dir = snapshot_dir / name
        device_dir.mkdir(parents=True, exist_ok=True)
        manifest["devices"][name] = {
            "ok": result.ok,
            "host": result.device.host,
            "role": result.device.role,
            "site": result.device.site,
            "error": result.error,
        }
        for key, output in result.outputs.items():
            (device_dir / f"{key}.txt").write_text(output, encoding="utf-8")
        for key, structured in result.structured_outputs.items():
            (device_dir / f"{key}.json").write_text(
                json.dumps(structured, indent=2, sort_keys=True, default=str),
                encoding="utf-8",
            )

    (snapshot_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return snapshot_dir


def collect_and_snapshot(settings: Settings, collector: Collector) -> Path:
    results = collector.collect_all()
    return write_snapshot(settings, results)


def compare_latest(settings: Settings) -> Path | None:
    settings.reports_path.mkdir(parents=True, exist_ok=True)
    snapshot_dirs = sorted([path for path in settings.snapshots_path.glob("20*-*-*") if path.is_dir()])
    if len(snapshot_dirs) < 2:
        return None

    previous, current = snapshot_dirs[-2], snapshot_dirs[-1]
    report_lines = [
        f"EVE AIOps daily comparison",
        f"Previous: {previous.name}",
        f"Current: {current.name}",
        "",
    ]

    important_changes = 0
    for current_file in sorted(current.glob("*/*.txt")):
        previous_file = previous / current_file.relative_to(current)
        if not previous_file.exists():
            important_changes += 1
            report_lines.append(f"[major] New output: {current_file.relative_to(current)}")
            continue

        old = previous_file.read_text(encoding="utf-8", errors="replace").splitlines()
        new = current_file.read_text(encoding="utf-8", errors="replace").splitlines()
        if old == new:
            continue

        rel = current_file.relative_to(current)
        severity = "major" if rel.name in {"bgp_summary.txt", "ospf_neighbors.txt", "nve_peers.txt"} else "minor"
        if severity == "major":
            important_changes += 1
        report_lines.append(f"[{severity}] Changed: {rel}")
        diff = difflib.unified_diff(old, new, fromfile=str(previous_file), tofile=str(current_file), lineterm="")
        report_lines.extend(list(diff)[:200])
        report_lines.append("")

    if important_changes == 0:
        report_lines.append("No major control-plane changes detected.")

    report_path = settings.reports_path / f"diff-{previous.name}-to-{current.name}.txt"
    report_path.write_text("\n".join(report_lines), encoding="utf-8")

    if important_changes > 0:
        send_report(
            settings,
            subject=f"[EVE AIOps] {important_changes} major change(s) detected",
            body=report_path.read_text(encoding="utf-8"),
        )

    return report_path
