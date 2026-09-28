from __future__ import annotations

import asyncio
import os
import re
import time
import urllib.request
from dataclasses import asdict
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .collector import Collector
from .inventory import load_inventory
from .parser import normalize_mac
from .settings import load_settings
from .snapshots import collect_and_snapshot, compare_latest

settings = load_settings()
devices = load_inventory()
collector = Collector(settings, devices)
scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
MRTG_TELEMETRY_METRICS_URL = os.getenv("AIOPS_TELEGRAF_METRICS_URL", "http://telegraf:9273/metrics")
MRTG_TELEMETRY_CACHE: dict[tuple[str, str, str], tuple[float, float]] = {}

app = FastAPI(title="EVE AIOps Agent", version="0.1.0")


def _hour_minute(value: str) -> tuple[int, int]:
    hour, minute = value.split(":", maxsplit=1)
    return int(hour), int(minute)


async def _collect_loop_once() -> None:
    await asyncio.to_thread(collector.collect_all)


async def _snapshot_once() -> None:
    await asyncio.to_thread(collect_and_snapshot, settings, collector)


async def _compare_once() -> None:
    await asyncio.to_thread(compare_latest, settings)


@app.on_event("startup")
async def startup() -> None:
    settings.snapshots_path.mkdir(parents=True, exist_ok=True)
    settings.reports_path.mkdir(parents=True, exist_ok=True)
    scheduler.add_job(
        _collect_loop_once,
        "interval",
        seconds=settings.collector_interval_seconds,
        id="collect_metrics",
        replace_existing=True,
        next_run_time=datetime.now(),
    )

    snap_hour, snap_minute = _hour_minute(settings.snapshot_time)
    scheduler.add_job(
        _snapshot_once,
        "cron",
        hour=snap_hour,
        minute=snap_minute,
        id="daily_snapshot",
        replace_existing=True,
    )

    compare_hour, compare_minute = _hour_minute(settings.compare_time)
    scheduler.add_job(
        _compare_once,
        "cron",
        hour=compare_hour,
        minute=compare_minute,
        id="daily_compare",
        replace_existing=True,
    )
    scheduler.start()


@app.on_event("shutdown")
async def shutdown() -> None:
    scheduler.shutdown(wait=False)


@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "ok"


@app.get("/metrics")
def prometheus_metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/state")
def structured_state() -> dict[str, object]:
    return {
        "devices": [
            {
                "name": result.device.name,
                "host": result.device.host,
                "role": result.device.role,
                "site": result.device.site,
                "ok": result.ok,
                "error": result.error,
                "duration_seconds": result.duration_seconds,
                "hardware": asdict(result.hardware),
                "hardware_components": [asdict(component) for component in result.hardware_components],
                "tcam_resources": [asdict(resource) for resource in result.tcam_resources],
                "resources": asdict(result.resources),
                "interfaces": result.interfaces,
                "mac_table": [asdict(entry) for entry in result.mac_table],
                "arp_table": [asdict(entry) for entry in result.arp_table],
                "lldp_neighbors": [asdict(neighbor) for neighbor in result.lldp_neighbors],
                "route_summary": [asdict(entry) for entry in result.route_summary],
                "bgp": [asdict(neighbor) for neighbor in result.bgp],
                "ospf": [asdict(neighbor) for neighbor in result.ospf],
                "vxlan": [asdict(peer) for peer in result.nve],
                "structured_commands": sorted(result.structured_outputs.keys()),
            }
            for result in sorted(collector.latest_results.values(), key=lambda item: item.device.name)
        ]
    }


@app.post("/collect", response_class=PlainTextResponse)
async def collect_now() -> str:
    await asyncio.to_thread(collector.collect_all)
    return "collection complete"


@app.post("/snapshot", response_class=PlainTextResponse)
async def snapshot_now() -> str:
    path = await asyncio.to_thread(collect_and_snapshot, settings, collector)
    return f"snapshot written: {path}"


@app.post("/compare", response_class=PlainTextResponse)
async def compare_now() -> str:
    path = await asyncio.to_thread(compare_latest, settings)
    return f"report written: {path}" if path else "not enough snapshots to compare"


@app.get("/tools", response_class=HTMLResponse)
def tools_view() -> str:
    return """
<!doctype html>
<html>
<head>
  <title>AI Networking OPS Tools</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root { --bg: #f6f8fb; --panel: #fff; --line: #d7dee8; --text: #17202a; --muted: #64748b; --green: #137333; --yellow: #b7791f; --red: #b42318; --blue: #075985; }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    header { padding: 20px 24px; background: #fff; border-bottom: 1px solid #d7dee8; }
    h1 { margin: 0; font-size: 20px; letter-spacing: 0; }
    main { padding: 20px 24px 28px; display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); align-items: start; }
    section { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; }
    h2 { margin: 0 0 12px; font-size: 16px; letter-spacing: 0; }
    label { display: block; color: #475569; font-size: 13px; margin-bottom: 6px; }
    input, select, button { height: 36px; border: 1px solid #cbd5e1; border-radius: 6px; padding: 0 10px; font: inherit; background: #fff; }
    input, select { width: 100%; }
    button { background: #0f172a; color: #fff; cursor: pointer; }
    .form-grid { display: grid; gap: 10px; grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .wide { grid-column: 1 / -1; }
    .actions { margin-top: 12px; display: flex; gap: 10px; align-items: center; }
    .result { min-height: 180px; margin-top: 14px; display: grid; gap: 12px; }
    .message { color: var(--muted); background: #f8fafc; border: 1px dashed var(--line); border-radius: 6px; padding: 14px; }
    .summary { display: flex; flex-wrap: wrap; gap: 8px; }
    .pill { display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--line); border-radius: 999px; padding: 4px 9px; color: #334155; background: #f8fafc; font-size: 12px; }
    .pill.good { color: var(--green); border-color: #b7e2c1; background: #f0fdf4; }
    .pill.warn { color: var(--yellow); border-color: #f2d49b; background: #fffbeb; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { padding: 8px 9px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
    th { background: #f8fafc; color: #475569; font-weight: 700; }
    .table-wrap { overflow: auto; border: 1px solid var(--line); border-radius: 6px; }
    h3 { margin: 2px 0 8px; font-size: 14px; }
    .path { display: grid; gap: 8px; }
    .step { border: 1px solid var(--line); border-radius: 6px; padding: 10px; background: #fff; }
    .step-title { font-weight: 700; margin-bottom: 4px; }
    .step-meta { color: var(--muted); font-size: 13px; }
    @media (max-width: 680px) { main { grid-template-columns: 1fr; padding: 14px; } .form-grid { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <header>
    <h1>AI Networking OPS Tools</h1>
  </header>
  <main>
    <section>
      <h2>MAC / IP Lookup</h2>
      <div class="form-grid">
        <div class="wide">
          <label for="macQuery">MAC or IP</label>
          <input id="macQuery" placeholder="aabb.cc80.b000 or 10.110.0.11">
        </div>
        <div>
          <label for="macDevice">Device</label>
          <select id="macDevice"></select>
        </div>
        <div>
          <label for="macVrf">VRF for IP lookup</label>
          <select id="macVrf"></select>
        </div>
      </div>
      <div class="actions"><button type="button" onclick="lookupMac()">Lookup</button></div>
      <div id="macResult" class="result"><div class="message">Waiting for query...</div></div>
    </section>
    <section>
      <h2>End-to-End Path</h2>
      <div class="form-grid">
        <div class="wide">
          <label for="srcQuery">Source MAC or IP</label>
          <input id="srcQuery" placeholder="10.110.0.11">
        </div>
        <div>
          <label for="srcDevice">Source Device</label>
          <select id="srcDevice"></select>
        </div>
        <div>
          <label for="srcVrf">Source VRF for IP</label>
          <select id="srcVrf"></select>
        </div>
        <div class="wide">
          <label for="dstQuery">Destination MAC or IP</label>
          <input id="dstQuery" placeholder="10.120.0.21">
        </div>
        <div>
          <label for="dstDevice">Destination Device</label>
          <select id="dstDevice"></select>
        </div>
        <div>
          <label for="dstVrf">Destination VRF for IP</label>
          <select id="dstVrf"></select>
        </div>
      </div>
      <div class="actions"><button type="button" onclick="lookupPath()">Trace Path</button></div>
      <div id="pathResult" class="result"><div class="message">Waiting for query...</div></div>
    </section>
  </main>
  <script>
    const deviceSelects = ["macDevice", "srcDevice", "dstDevice"];
    const vrfSelects = ["macVrf", "srcVrf", "dstVrf"];

    loadOptions();
    bindVrfToggle("macQuery", "macVrf");
    bindVrfToggle("srcQuery", "srcVrf");
    bindVrfToggle("dstQuery", "dstVrf");

    async function lookupMac() {
      const query = document.getElementById("macQuery").value.trim();
      const params = new URLSearchParams({
        query,
        device: document.getElementById("macDevice").value,
        vrf: isIp(query) ? document.getElementById("macVrf").value : "all",
      });
      await renderLookup("/tools/mac?" + params.toString(), "macResult");
    }

    async function lookupPath() {
      const src = document.getElementById("srcQuery").value.trim();
      const dst = document.getElementById("dstQuery").value.trim();
      const params = new URLSearchParams({
        src,
        dst,
        src_device: document.getElementById("srcDevice").value,
        src_vrf: isIp(src) ? document.getElementById("srcVrf").value : "all",
        dst_device: document.getElementById("dstDevice").value,
        dst_vrf: isIp(dst) ? document.getElementById("dstVrf").value : "all",
      });
      await renderPath("/tools/path?" + params.toString(), "pathResult");
    }

    function bindVrfToggle(queryId, vrfId) {
      const input = document.getElementById(queryId);
      input.addEventListener("input", () => updateVrfState(queryId, vrfId));
      updateVrfState(queryId, vrfId);
    }

    function updateVrfState(queryId, vrfId) {
      const value = document.getElementById(queryId).value.trim();
      const select = document.getElementById(vrfId);
      const enabled = !value || isIp(value);
      select.disabled = !enabled;
      if (!enabled) select.value = "all";
      select.title = enabled ? "Used for IP lookup" : "VRF is not used for MAC lookup";
    }

    async function loadOptions() {
      try {
        const response = await fetch("/tools/options", { cache: "no-store" });
        const payload = await response.json();
        for (const id of deviceSelects) fillSelect(id, payload.devices || [], "All devices");
        for (const id of vrfSelects) fillSelect(id, payload.vrfs || [], "All VRFs");
      } catch (error) {
        for (const id of deviceSelects) fillSelect(id, [], "All devices");
        for (const id of vrfSelects) fillSelect(id, [], "All VRFs");
      }
    }

    function fillSelect(id, values, allLabel) {
      const select = document.getElementById(id);
      select.innerHTML = "";
      select.append(new Option(allLabel, "all"));
      for (const value of values) select.append(new Option(value, value));
    }

    async function renderLookup(url, target) {
      const node = document.getElementById(target);
      node.innerHTML = '<div class="message">Loading...</div>';
      try {
        const response = await fetch(url, { cache: "no-store" });
        const payload = await response.json();
        node.innerHTML = lookupHtml(payload);
      } catch (error) {
        node.innerHTML = '<div class="message">Request failed: ' + escapeHtml(String(error)) + '</div>';
      }
    }

    async function renderPath(url, target) {
      const node = document.getElementById(target);
      node.innerHTML = '<div class="message">Loading...</div>';
      try {
        const response = await fetch(url, { cache: "no-store" });
        const payload = await response.json();
        node.innerHTML = pathHtml(payload);
      } catch (error) {
        node.innerHTML = '<div class="message">Request failed: ' + escapeHtml(String(error)) + '</div>';
      }
    }

    function lookupHtml(payload) {
      const summary = [
        pill(payload.found ? "Found" : "Not found", payload.found ? "good" : "warn"),
        pill("Query: " + (payload.query || "")),
        pill("Device: " + (payload.device_filter || "all")),
        pill("VRF: " + (payload.vrf_filter || "all")),
        payload.resolved_ip ? pill("IP: " + payload.resolved_ip) : "",
        payload.resolved_mac ? pill("MAC: " + payload.resolved_mac) : "",
      ].join("");
      return '<div class="summary">' + summary + '</div>'
        + tableSection("ARP Matches", payload.arp_matches || [], ["device", "vrf", "ip", "mac", "interface", "site", "role"])
        + tableSection("Local MAC Matches", payload.local_matches || [], ["device", "vlan", "mac", "port", "site", "role"])
        + tableSection("Overlay MAC Matches", payload.overlay_matches || [], ["device", "vlan", "mac", "port", "site", "role"])
        + tableSection("CLF Matches", payload.clf_matches || [], ["device", "vlan", "mac", "port", "site", "location_type"]);
    }

    function pathHtml(payload) {
      const source = endpointSummary("Source", payload.source || {});
      const destination = endpointSummary("Destination", payload.destination || {});
      const steps = payload.path || [];
      const path = steps.length
        ? '<div class="path">' + steps.map((step, index) => stepHtml(step, index)).join("") + '</div>'
        : '<div class="message">No path could be inferred from current MAC, ARP, and LLDP data.</div>';
      const notes = ((payload.evidence || {}).notes || []).map(note => '<span class="pill">' + escapeHtml(note) + '</span>').join("");
      return source + destination + '<h3>Path</h3>' + path + '<div class="summary">' + notes + '</div>';
    }

    function endpointSummary(title, endpoint) {
      return '<h3>' + title + '</h3><div class="summary">'
        + pill("Query: " + (endpoint.query || ""))
        + (endpoint.ip ? pill("IP: " + endpoint.ip) : "")
        + (endpoint.mac ? pill("MAC: " + endpoint.mac) : "")
        + pill("ARP: " + ((endpoint.arp_matches || []).length))
        + pill("MAC table: " + ((endpoint.mac_matches || []).length))
        + '</div>';
    }

    function stepHtml(step, index) {
      const title = step.label || String(step.type || "step").replaceAll("_", " ");
      const details = Object.entries(step)
        .filter(([key]) => !["type", "label"].includes(key))
        .map(([key, value]) => '<span class="pill">' + escapeHtml(key) + ': ' + escapeHtml(String(value ?? "")) + '</span>')
        .join("");
      return '<div class="step"><div class="step-title">' + (index + 1) + '. ' + escapeHtml(title) + '</div><div class="step-meta summary">' + details + '</div></div>';
    }

    function tableSection(title, rows, columns) {
      if (!rows.length) return '<div><h3>' + escapeHtml(title) + '</h3><div class="message">No matches</div></div>';
      const head = columns.map(col => '<th>' + escapeHtml(label(col)) + '</th>').join("");
      const body = rows.map(row => '<tr>' + columns.map(col => '<td>' + escapeHtml(String(row[col] ?? "")) + '</td>').join("") + '</tr>').join("");
      return '<div><h3>' + escapeHtml(title) + '</h3><div class="table-wrap"><table><thead><tr>' + head + '</tr></thead><tbody>' + body + '</tbody></table></div></div>';
    }

    function pill(text, cls) {
      return '<span class="pill ' + (cls || "") + '">' + escapeHtml(text) + '</span>';
    }

    function label(value) {
      return value.replaceAll("_", " ").replace(/\\b\\w/g, char => char.toUpperCase());
    }

    function escapeHtml(value) {
      return value.replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
    }

    function isIp(value) {
      const parts = value.split(".");
      if (parts.length !== 4) return false;
      return parts.every(part => /^\\d+$/.test(part) && Number(part) >= 0 && Number(part) <= 255);
    }
  </script>
</body>
</html>
"""


@app.get("/tools/mac")
def mac_lookup(query: str, device: str = "all", vrf: str = "all") -> dict[str, object]:
    return _lookup_mac(query, device_filter=device, vrf_filter=vrf)


@app.get("/tools/path")
def path_lookup(
    src: str,
    dst: str,
    src_device: str = "all",
    src_vrf: str = "all",
    dst_device: str = "all",
    dst_vrf: str = "all",
) -> dict[str, object]:
    source = _resolve_endpoint(src, device_filter=src_device, vrf_filter=src_vrf)
    destination = _resolve_endpoint(dst, device_filter=dst_device, vrf_filter=dst_vrf)
    source_location = _best_local_location(source)
    destination_location = _best_local_location(destination)
    path = _build_path_steps(source, destination, source_location, destination_location)
    return {
        "source": source,
        "destination": destination,
        "path": path,
        "evidence": {
            "lldp_path": _shortest_lldp_path(
                source_location.get("device") if source_location else "",
                destination_location.get("device") if destination_location else "",
            ),
            "notes": [
                "MAC entries on Vxlan/Vx ports indicate remote overlay learning.",
                "Physical/local ports are preferred as endpoint attachment points.",
                "If LLDP is incomplete, the path is a best-effort logical path.",
            ],
        },
    }


@app.get("/tools/options")
def tools_options() -> dict[str, object]:
    vrfs = sorted(
        {
            entry.vrf
            for result in collector.latest_results.values()
            for entry in result.arp_table
            if entry.vrf
        }
    )
    return {
        "devices": sorted(device.name for device in devices),
        "vrfs": vrfs,
    }


def _lookup_mac(query: str, device_filter: str = "all", vrf_filter: str = "all") -> dict[str, object]:
    resolved = _resolve_endpoint(query, device_filter=device_filter, vrf_filter=vrf_filter)
    matches = resolved.get("mac_matches", [])
    arp_matches = resolved.get("arp_matches", [])
    clf_matches = [match for match in matches if match.get("role") == "clf"]
    return {
        "query": query,
        "normalized_query": resolved.get("mac") or query,
        "resolved_ip": resolved.get("ip"),
        "resolved_mac": resolved.get("mac"),
        "device_filter": _normalize_filter(device_filter),
        "vrf_filter": _normalize_filter(vrf_filter),
        "found": bool(matches or arp_matches),
        "clf_matches": clf_matches,
        "local_matches": [match for match in matches if match.get("location_type") == "local"],
        "overlay_matches": [match for match in matches if match.get("location_type") == "overlay"],
        "all_matches": matches,
        "arp_matches": arp_matches,
    }


def _resolve_endpoint(query: str, device_filter: str = "all", vrf_filter: str = "all") -> dict[str, object]:
    query = query.strip()
    normalized_device_filter = _normalize_filter(device_filter)
    normalized_vrf_filter = _normalize_filter(vrf_filter)
    ip = query if _is_ip(query) else ""
    mac = ""
    arp_matches: list[dict[str, object]] = []

    if ip:
        for result in _filtered_results(normalized_device_filter):
            for entry in result.arp_table:
                if entry.ip == ip and _matches_filter(entry.vrf, normalized_vrf_filter):
                    mac = entry.mac
                    arp_matches.append(
                        {
                            "device": result.device.name,
                            "role": result.device.role,
                            "site": result.device.site,
                            **asdict(entry),
                        }
                    )
    else:
        mac = normalize_mac(query)
        for result in _filtered_results(normalized_device_filter):
            for entry in result.arp_table:
                if entry.mac == mac:
                    arp_matches.append(
                        {
                            "device": result.device.name,
                            "role": result.device.role,
                            "site": result.device.site,
                            **asdict(entry),
                        }
                    )

    mac_matches: list[dict[str, object]] = []
    if mac:
        for result in _filtered_results(normalized_device_filter):
            for entry in result.mac_table:
                if entry.mac == mac:
                    mac_matches.append(
                        {
                            "device": result.device.name,
                            "role": result.device.role,
                            "site": result.device.site,
                            "location_type": _mac_location_type(entry.port),
                            **asdict(entry),
                        }
                    )

    return {
        "query": query,
        "device_filter": normalized_device_filter,
        "vrf_filter": normalized_vrf_filter,
        "ip": ip or None,
        "mac": mac or None,
        "arp_matches": sorted(arp_matches, key=lambda item: (item.get("vrf") != "TENANT", item["site"], item["device"], item["ip"])),
        "mac_matches": sorted(mac_matches, key=lambda item: (item["location_type"] != "local", item["site"], item["device"], item["vlan"])),
    }


def _filtered_results(device_filter: str):
    for result in collector.latest_results.values():
        if _matches_filter(result.device.name, device_filter):
            yield result


def _normalize_filter(value: str | None) -> str:
    value = (value or "all").strip()
    return value if value else "all"


def _matches_filter(value: object, selected: str) -> bool:
    return selected == "all" or str(value) == selected


def _best_local_location(endpoint: dict[str, object]) -> dict[str, object]:
    matches = endpoint.get("mac_matches", [])
    if not isinstance(matches, list):
        return {}
    local = [match for match in matches if match.get("location_type") == "local"]
    if local:
        return local[0]
    arp_location = _best_arp_location(endpoint)
    if arp_location:
        return arp_location
    overlay = [match for match in matches if match.get("location_type") == "overlay"]
    return overlay[0] if overlay else {}


def _best_arp_location(endpoint: dict[str, object]) -> dict[str, object]:
    arp_matches = endpoint.get("arp_matches", [])
    if not isinstance(arp_matches, list):
        return {}
    learned_candidates = [
        match
        for match in arp_matches
        if "not learned" not in str(match.get("interface", "")).lower()
        and not str(match.get("interface", "")).lower().startswith("management")
    ]
    fallback_candidates = [
        match
        for match in arp_matches
        if not str(match.get("interface", "")).lower().startswith("management")
    ]
    candidates = learned_candidates or fallback_candidates
    if not candidates:
        return {}
    match = sorted(candidates, key=lambda item: (item.get("vrf") != "TENANT", item["site"], item["device"]))[0]
    interface = str(match.get("interface", ""))
    learned = "not learned" not in interface.lower()
    return {
        "device": match.get("device"),
        "role": match.get("role"),
        "site": match.get("site"),
        "vlan": interface.split(",", maxsplit=1)[0],
        "port": match.get("interface"),
        "location_type": "arp" if learned else "l3_gateway",
        "evidence": "arp" if learned else "arp_gateway_only",
        "ip": match.get("ip"),
        "mac": match.get("mac"),
        "vrf": match.get("vrf"),
    }


def _build_path_steps(
    source_endpoint: dict[str, object],
    destination_endpoint: dict[str, object],
    source: dict[str, object],
    destination: dict[str, object],
) -> list[dict[str, object]]:
    steps: list[dict[str, object]] = []
    hop = 1

    source_resolution = _ip_resolution_step(source_endpoint, "source", hop)
    if source_resolution:
        steps.append(source_resolution)
        hop += 1

    if not source or not destination:
        if source:
            steps.append(_attachment_step("source", source, hop))
            hop += 1
        steps.append(
            {
                "type": "unknown",
                "label": f"Hop {hop}: location incomplete",
                "detail": "source or destination location was not found in current ARP/MAC/LLDP data",
                "source_found": bool(source),
                "destination_found": bool(destination),
            }
        )
        hop += 1
        if destination:
            steps.append(_attachment_step("destination", destination, hop))
            hop += 1
        destination_resolution = _ip_resolution_step(destination_endpoint, "destination", hop)
        if destination_resolution:
            steps.append(destination_resolution)
        return steps

    steps.append(_attachment_step("source", source, hop))
    hop += 1

    if source.get("device") == destination.get("device"):
        steps.append(
            {
                "type": "same_device_switching",
                "label": f"Hop {hop}: switch locally on {source.get('device')}",
                "device": source.get("device"),
                "detail": "source and destination MACs are local on the same device",
            }
        )
        hop += 1
    else:
        lldp_path = _shortest_lldp_path(str(source.get("device", "")), str(destination.get("device", "")))
        if lldp_path:
            for edge in lldp_path:
                steps.append(
                    {
                        "type": "underlay_hop",
                        "label": f"Hop {len(steps) + 1}: {edge.get('from')} -> {edge.get('to')}",
                        **edge,
                    }
                )
        else:
            steps.append(
                {
                    "type": "overlay_or_unknown",
                    "label": f"Hop {hop}: overlay or unknown transit",
                    "from": source.get("device"),
                    "to": destination.get("device"),
                    "detail": "no complete LLDP path found; traffic is inferred through EVPN/VXLAN overlay",
                }
            )
            hop += 1

    steps.append(_attachment_step("destination", destination, len(steps) + 1))
    destination_resolution = _ip_resolution_step(destination_endpoint, "destination", len(steps) + 1)
    if destination_resolution:
        steps.append(destination_resolution)
    return steps


def _ip_resolution_step(endpoint: dict[str, object], side: str, hop: int) -> dict[str, object]:
    ip = endpoint.get("ip")
    if not ip:
        return {}
    arp_matches = endpoint.get("arp_matches", [])
    arp_match = arp_matches[0] if isinstance(arp_matches, list) and arp_matches else {}
    return {
        "type": f"{side}_ip_resolution",
        "label": f"Hop {hop}: {side} IP to MAC resolution",
        "device": arp_match.get("device"),
        "site": arp_match.get("site"),
        "role": arp_match.get("role"),
        "vrf": arp_match.get("vrf") or endpoint.get("vrf_filter"),
        "ip": ip,
        "mac": endpoint.get("mac"),
        "interface": arp_match.get("interface"),
        "evidence": "arp",
    }


def _attachment_step(side: str, location: dict[str, object], hop: int) -> dict[str, object]:
    device = location.get("device")
    interface = location.get("port")
    label_side = "source" if side == "source" else "destination"
    return {
        "type": f"{side}_attachment",
        "label": f"Hop {hop}: {label_side} attachment on {device}",
        "device": device,
        "role": location.get("role"),
        "site": location.get("site"),
        "interface": interface,
        "vlan": location.get("vlan"),
        "location_type": location.get("location_type"),
        "evidence": location.get("evidence"),
    }


def _shortest_lldp_path(start: str, end: str) -> list[dict[str, object]]:
    if not start or not end or start == end:
        return []

    graph: dict[str, list[dict[str, str]]] = {}
    known_devices = {device.name for device in devices}
    for result in collector.latest_results.values():
        for neighbor in result.lldp_neighbors:
            if neighbor.neighbor not in known_devices:
                continue
            graph.setdefault(result.device.name, []).append(
                {
                    "from": result.device.name,
                    "to": neighbor.neighbor,
                    "local_interface": neighbor.local_interface,
                    "neighbor_interface": neighbor.neighbor_interface,
                }
            )
            graph.setdefault(neighbor.neighbor, []).append(
                {
                    "from": neighbor.neighbor,
                    "to": result.device.name,
                    "local_interface": neighbor.neighbor_interface,
                    "neighbor_interface": neighbor.local_interface,
                }
            )

    queue: list[tuple[str, list[dict[str, str]]]] = [(start, [])]
    seen = {start}
    while queue:
        node, path = queue.pop(0)
        for edge in graph.get(node, []):
            next_node = edge["to"]
            if next_node in seen:
                continue
            next_path = [*path, edge]
            if next_node == end:
                return next_path
            seen.add(next_node)
            queue.append((next_node, next_path))
    return []


def _mac_location_type(port: str) -> str:
    normalized = port.lower()
    if normalized.startswith(("vx", "vxlan")):
        return "overlay"
    if normalized == "cpu":
        return "control_plane"
    return "local"


def _is_ip(value: str) -> bool:
    parts = value.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(part) <= 255 for part in parts)
    except ValueError:
        return False


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    rows = []
    for device in devices:
        result = collector.latest_results.get(device.name)
        status = "unknown"
        detail = "collection has not run yet"
        if result:
            status = "up" if result.ok else "down"
            detail = result.error or f"{result.duration_seconds:.1f}s"
        rows.append(
            f"<tr><td>{device.site}</td><td>{device.role}</td><td>{device.name}</td>"
            f"<td>{device.host}</td><td class='{status}'>{status}</td><td>{detail}</td></tr>"
        )

    return f"""
<!doctype html>
<html>
<head>
  <title>EVE AIOps Agent</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #17202a; }}
    a {{ color: #075985; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
    th, td {{ border-bottom: 1px solid #d7dee8; padding: 10px; text-align: left; }}
    th {{ background: #f4f7fb; }}
    .up {{ color: #137333; font-weight: 700; }}
    .down {{ color: #b42318; font-weight: 700; }}
    .unknown {{ color: #6b7280; font-weight: 700; }}
  </style>
</head>
<body>
  <h1>EVE AIOps Agent</h1>
  <p><a href="/mrtg">MRTG-style interface view</a> | <a href="/metrics">Prometheus metrics</a></p>
  <table>
    <thead><tr><th>Site</th><th>Role</th><th>Device</th><th>Host</th><th>Status</th><th>Detail</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
</body>
</html>
"""


@app.get("/mrtg", response_class=HTMLResponse)
def mrtg_view() -> str:
    return """
<!doctype html>
<html>
<head>
  <title>MRTG Interface View</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {
      color-scheme: light;
      --text: #17202a;
      --muted: #64748b;
      --line: #d7dee8;
      --panel: #ffffff;
      --bg: #f6f8fb;
      --green: #17803d;
      --blue: #2563eb;
      --red: #b42318;
      --yellow: #b7791f;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    header {
      position: sticky;
      top: 0;
      z-index: 3;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 18px 24px;
      background: rgba(255, 255, 255, 0.94);
      border-bottom: 1px solid var(--line);
      backdrop-filter: blur(10px);
    }
    h1 {
      margin: 0;
      font-size: 20px;
      font-weight: 700;
      letter-spacing: 0;
    }
    .toolbar {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    select, button {
      height: 34px;
      border: 1px solid #cbd5e1;
      background: #fff;
      border-radius: 6px;
      padding: 0 10px;
      color: var(--text);
      font: inherit;
    }
    button { cursor: pointer; }
    main {
      padding: 20px 24px 28px;
    }
    .statusbar {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 16px;
    }
    .legend {
      display: flex;
      gap: 14px;
      align-items: center;
      flex-wrap: wrap;
    }
    .legend span {
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }
    .dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      display: inline-block;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
      gap: 16px;
    }
    .chart {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
    }
    .chart-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
    }
    .title {
      min-width: 0;
      font-weight: 700;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .meta {
      display: flex;
      align-items: center;
      gap: 10px;
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }
    .state {
      border-radius: 999px;
      padding: 2px 8px;
      font-weight: 700;
      color: #fff;
    }
    .state.up { background: var(--green); }
    .state.down { background: var(--red); }
    canvas {
      display: block;
      width: 100%;
      height: 220px;
    }
    .empty {
      padding: 48px 24px;
      color: var(--muted);
      text-align: center;
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    @media (max-width: 640px) {
      header { align-items: flex-start; flex-direction: column; }
      main { padding: 14px; }
      .grid { grid-template-columns: 1fr; }
      canvas { height: 190px; }
    }
  </style>
</head>
<body>
  <header>
    <h1>MRTG Interface View</h1>
    <div class="toolbar">
      <select id="deviceFilter" aria-label="Device"></select>
      <button id="clearHistory" type="button">Clear History</button>
    </div>
  </header>
  <main>
    <div class="statusbar">
      <div id="summary">Loading...</div>
      <div class="legend">
        <span><i class="dot" style="background: var(--green)"></i>RX</span>
        <span><i class="dot" style="background: var(--blue)"></i>TX</span>
        <span><i class="dot" style="background: var(--red)"></i>Down</span>
      </div>
    </div>
    <section id="charts" class="grid"></section>
  </main>
  <script>
    const HISTORY_KEY = "eve-aiops-mrtg-history-v2";
    const MAX_POINTS = 120;
    const REFRESH_MS = 10000;
    const state = {
      samples: loadHistory(),
      latest: [],
      filter: localStorage.getItem("eve-aiops-mrtg-device") || "all",
    };

    const charts = document.getElementById("charts");
    const summary = document.getElementById("summary");
    const deviceFilter = document.getElementById("deviceFilter");
    document.getElementById("clearHistory").addEventListener("click", () => {
      state.samples = {};
      localStorage.removeItem(HISTORY_KEY);
      render();
    });
    deviceFilter.addEventListener("change", () => {
      state.filter = deviceFilter.value;
      localStorage.setItem("eve-aiops-mrtg-device", state.filter);
      render();
    });

    async function refresh() {
      try {
        const response = await fetch("/mrtg/data", { cache: "no-store" });
        const payload = await response.json();
        state.latest = payload.interfaces || [];
        mergeSamples(payload.timestamp, state.latest);
        saveHistory();
        updateDeviceFilter();
        render();
      } catch (error) {
        summary.textContent = "Unable to load interface data";
      }
    }

    function mergeSamples(timestamp, interfaces) {
      for (const item of interfaces) {
        const key = item.device + "::" + item.interface;
        if (!state.samples[key]) state.samples[key] = [];
        const points = state.samples[key];
        const last = points[points.length - 1];
        if (!last || last.t !== timestamp) {
          points.push({
            t: timestamp,
            rx: Number(item.rx_bps || 0),
            tx: Number(item.tx_bps || 0),
            up: Boolean(item.oper_up),
          });
          if (points.length > MAX_POINTS) points.splice(0, points.length - MAX_POINTS);
        }
      }
    }

    function updateDeviceFilter() {
      const devices = Array.from(new Set(state.latest.map(item => item.device))).sort();
      const current = deviceFilter.value || state.filter;
      deviceFilter.innerHTML = "";
      deviceFilter.append(new Option("All devices", "all"));
      for (const device of devices) deviceFilter.append(new Option(device, device));
      deviceFilter.value = devices.includes(current) ? current : "all";
      state.filter = deviceFilter.value;
    }

    function render() {
      const items = state.latest
        .filter(item => state.filter === "all" || item.device === state.filter)
        .sort((a, b) => (a.device + a.interface).localeCompare(b.device + b.interface));
      summary.textContent = `${items.length} interfaces, last refresh ${new Date().toLocaleTimeString()}`;
      charts.innerHTML = "";
      if (!items.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = "No interface samples yet";
        charts.append(empty);
        return;
      }
      for (const item of items) {
        const key = item.device + "::" + item.interface;
        const card = document.createElement("article");
        card.className = "chart";
        card.innerHTML = `
          <div class="chart-head">
            <div class="title" title="${escapeHtml(item.device)} ${escapeHtml(item.interface)}">
              ${escapeHtml(item.device)} / ${escapeHtml(item.interface)}
            </div>
            <div class="meta">
              <span>RX ${formatBps(item.rx_bps)}</span>
              <span>TX ${formatBps(item.tx_bps)}</span>
              <span>${escapeHtml(item.traffic_source || "unknown")}</span>
              <span class="state ${item.oper_up ? "up" : "down"}">${item.oper_up ? "up" : "down"}</span>
            </div>
          </div>
          <canvas></canvas>
        `;
        charts.append(card);
        drawChart(card.querySelector("canvas"), state.samples[key] || []);
      }
    }

    function drawChart(canvas, points) {
      const scale = window.devicePixelRatio || 1;
      const rect = canvas.getBoundingClientRect();
      canvas.width = Math.max(1, Math.floor(rect.width * scale));
      canvas.height = Math.max(1, Math.floor(rect.height * scale));
      const ctx = canvas.getContext("2d");
      ctx.scale(scale, scale);
      const width = rect.width;
      const height = rect.height;
      const pad = { top: 18, right: 18, bottom: 28, left: 58 };
      const plotW = width - pad.left - pad.right;
      const plotH = height - pad.top - pad.bottom;
      const max = Math.max(1, ...points.flatMap(p => [p.rx, p.tx]));

      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, width, height);

      ctx.strokeStyle = "#e2e8f0";
      ctx.lineWidth = 1;
      ctx.font = "12px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
      ctx.fillStyle = "#64748b";
      for (let i = 0; i <= 4; i++) {
        const y = pad.top + (plotH / 4) * i;
        ctx.beginPath();
        ctx.moveTo(pad.left, y);
        ctx.lineTo(width - pad.right, y);
        ctx.stroke();
        ctx.fillText(formatBps(max * (1 - i / 4)), 10, y + 4);
      }

      drawSeries(ctx, points, "rx", "#17803d", pad, plotW, plotH, max);
      drawSeries(ctx, points, "tx", "#2563eb", pad, plotW, plotH, max);

      ctx.strokeStyle = "#94a3b8";
      ctx.beginPath();
      ctx.moveTo(pad.left, pad.top);
      ctx.lineTo(pad.left, height - pad.bottom);
      ctx.lineTo(width - pad.right, height - pad.bottom);
      ctx.stroke();
    }

    function drawSeries(ctx, points, field, color, pad, plotW, plotH, max) {
      if (!points.length) return;
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.beginPath();
      points.forEach((point, index) => {
        const x = pad.left + (points.length === 1 ? plotW : (plotW * index) / (points.length - 1));
        const y = pad.top + plotH - (Math.max(0, point[field]) / max) * plotH;
        if (index === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
    }

    function formatBps(value) {
      const num = Number(value || 0);
      if (num >= 1000000000) return (num / 1000000000).toFixed(2) + " Gbps";
      if (num >= 1000000) return (num / 1000000).toFixed(2) + " Mbps";
      if (num >= 1000) return (num / 1000).toFixed(2) + " Kbps";
      return Math.round(num) + " bps";
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, ch => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }[ch]));
    }

    function loadHistory() {
      try {
        return JSON.parse(localStorage.getItem(HISTORY_KEY) || "{}");
      } catch {
        return {};
      }
    }

    function saveHistory() {
      localStorage.setItem(HISTORY_KEY, JSON.stringify(state.samples));
    }

    refresh();
    setInterval(refresh, REFRESH_MS);
    window.addEventListener("resize", render);
  </script>
</body>
</html>
"""


@app.get("/mrtg/data")
def mrtg_data() -> dict[str, object]:
    interfaces = []
    telemetry_rates = _mrtg_telemetry_rates()
    for result in sorted(collector.latest_results.values(), key=lambda item: item.device.name):
        for iface in result.interfaces:
            telemetry = telemetry_rates.get((result.device.name, str(iface["name"])))
            interfaces.append(
                {
                    "device": result.device.name,
                    "interface": iface["name"],
                    "admin_up": bool(iface["admin_up"]),
                    "oper_up": bool(iface["oper_up"]),
                    "rx_bps": float(telemetry.get("rx_bps") if telemetry else iface["rx_bps"] or 0),
                    "tx_bps": float(telemetry.get("tx_bps") if telemetry else iface["tx_bps"] or 0),
                    "traffic_source": "telemetry" if telemetry else "agent",
                }
            )
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "interfaces": interfaces,
    }


def _mrtg_telemetry_rates() -> dict[tuple[str, str], dict[str, float]]:
    host_to_device = {device.host: device.name for device in devices}
    counters: dict[tuple[str, str, str], float] = {}
    now = time.time()
    try:
        with urllib.request.urlopen(MRTG_TELEMETRY_METRICS_URL, timeout=3) as response:
            payload = response.read().decode("utf-8", errors="ignore")
    except Exception:
        return {}

    for line in payload.splitlines():
        metric = _parse_telegraf_interface_octet_metric(line)
        if not metric:
            continue
        source, interface, direction, value = metric
        device = host_to_device.get(source)
        if not device and ":" in source:
            device = host_to_device.get(source.rsplit(":", 1)[0])
        if not device:
            continue
        counters[(device, interface, direction)] = value

    rates: dict[tuple[str, str], dict[str, float]] = {}
    for key, value in counters.items():
        previous = MRTG_TELEMETRY_CACHE.get(key)
        MRTG_TELEMETRY_CACHE[key] = (value, now)
        if not previous:
            continue
        previous_value, previous_time = previous
        elapsed = now - previous_time
        if elapsed <= 0 or value < previous_value:
            continue
        device, interface, direction = key
        field = "rx_bps" if direction == "in" else "tx_bps"
        rates.setdefault((device, interface), {})[field] = ((value - previous_value) * 8) / elapsed
    return {
        key: value
        for key, value in rates.items()
        if "rx_bps" in value or "tx_bps" in value
    }


def _parse_telegraf_interface_octet_metric(line: str) -> tuple[str, str, str, float] | None:
    match = re.match(r"^arista_interface_counters_(in|out)_octets\{([^}]*)\}\s+([0-9.eE+-]+)$", line)
    if not match:
        return None
    labels = _parse_prometheus_labels(match.group(2))
    source = labels.get("source", "")
    interface = labels.get("name", "")
    if not source or not interface:
        return None
    try:
        value = float(match.group(3))
    except ValueError:
        return None
    return source, interface, match.group(1), value


def _parse_prometheus_labels(raw: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    for key, value in re.findall(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:\\.|[^"])*)"', raw):
        labels[key] = value.replace('\\"', '"').replace("\\\\", "\\")
    return labels
