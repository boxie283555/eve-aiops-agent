from __future__ import annotations

import asyncio
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
  <title>EVE AIOps Tools</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body { margin: 0; background: #f6f8fb; color: #17202a; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    header { padding: 20px 24px; background: #fff; border-bottom: 1px solid #d7dee8; }
    h1 { margin: 0; font-size: 20px; letter-spacing: 0; }
    main { padding: 20px 24px 28px; display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); }
    section { background: #fff; border: 1px solid #d7dee8; border-radius: 8px; padding: 16px; }
    h2 { margin: 0 0 12px; font-size: 16px; letter-spacing: 0; }
    label { display: block; color: #475569; font-size: 13px; margin-bottom: 6px; }
    input, button { height: 36px; border: 1px solid #cbd5e1; border-radius: 6px; padding: 0 10px; font: inherit; }
    input { width: 100%; margin-bottom: 10px; }
    button { background: #0f172a; color: #fff; cursor: pointer; }
    pre { min-height: 180px; overflow: auto; background: #0f172a; color: #e5e7eb; border-radius: 6px; padding: 12px; font-size: 12px; line-height: 1.5; }
  </style>
</head>
<body>
  <header>
    <h1>EVE AIOps Tools</h1>
  </header>
  <main>
    <section>
      <h2>MAC / IP Lookup</h2>
      <label for="macQuery">MAC or IP</label>
      <input id="macQuery" placeholder="aabb.cc80.b000 or 10.110.0.11">
      <button type="button" onclick="lookupMac()">Lookup</button>
      <pre id="macResult">Waiting for query...</pre>
    </section>
    <section>
      <h2>End-to-End Path</h2>
      <label for="srcQuery">Source MAC or IP</label>
      <input id="srcQuery" placeholder="10.110.0.11">
      <label for="dstQuery">Destination MAC or IP</label>
      <input id="dstQuery" placeholder="10.120.0.21">
      <button type="button" onclick="lookupPath()">Trace Path</button>
      <pre id="pathResult">Waiting for query...</pre>
    </section>
  </main>
  <script>
    async function lookupMac() {
      const query = document.getElementById("macQuery").value.trim();
      await renderJson("/tools/mac?query=" + encodeURIComponent(query), "macResult");
    }
    async function lookupPath() {
      const src = document.getElementById("srcQuery").value.trim();
      const dst = document.getElementById("dstQuery").value.trim();
      await renderJson("/tools/path?src=" + encodeURIComponent(src) + "&dst=" + encodeURIComponent(dst), "pathResult");
    }
    async function renderJson(url, target) {
      const node = document.getElementById(target);
      node.textContent = "Loading...";
      try {
        const response = await fetch(url, { cache: "no-store" });
        node.textContent = JSON.stringify(await response.json(), null, 2);
      } catch (error) {
        node.textContent = "Request failed: " + error;
      }
    }
  </script>
</body>
</html>
"""


@app.get("/tools/mac")
def mac_lookup(query: str) -> dict[str, object]:
    return _lookup_mac(query)


@app.get("/tools/path")
def path_lookup(src: str, dst: str) -> dict[str, object]:
    source = _resolve_endpoint(src)
    destination = _resolve_endpoint(dst)
    source_location = _best_local_location(source)
    destination_location = _best_local_location(destination)
    path = _build_path_steps(source_location, destination_location)
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


def _lookup_mac(query: str) -> dict[str, object]:
    resolved = _resolve_endpoint(query)
    matches = resolved.get("mac_matches", [])
    arp_matches = resolved.get("arp_matches", [])
    clf_matches = [match for match in matches if match.get("role") == "clf"]
    return {
        "query": query,
        "normalized_query": resolved.get("mac") or query,
        "resolved_ip": resolved.get("ip"),
        "resolved_mac": resolved.get("mac"),
        "found": bool(matches or arp_matches),
        "clf_matches": clf_matches,
        "local_matches": [match for match in matches if match.get("location_type") == "local"],
        "overlay_matches": [match for match in matches if match.get("location_type") == "overlay"],
        "all_matches": matches,
        "arp_matches": arp_matches,
    }


def _resolve_endpoint(query: str) -> dict[str, object]:
    query = query.strip()
    ip = query if _is_ip(query) else ""
    mac = ""
    arp_matches: list[dict[str, object]] = []

    if ip:
        for result in collector.latest_results.values():
            for entry in result.arp_table:
                if entry.ip == ip:
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
        for result in collector.latest_results.values():
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
        for result in collector.latest_results.values():
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
        "ip": ip or None,
        "mac": mac or None,
        "arp_matches": sorted(arp_matches, key=lambda item: (item.get("vrf") != "TENANT", item["site"], item["device"], item["ip"])),
        "mac_matches": sorted(mac_matches, key=lambda item: (item["location_type"] != "local", item["site"], item["device"], item["vlan"])),
    }


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
    candidates = [
        match
        for match in arp_matches
        if "not learned" not in str(match.get("interface", "")).lower()
        and not str(match.get("interface", "")).lower().startswith("management")
    ]
    if not candidates:
        return {}
    match = sorted(candidates, key=lambda item: (item.get("vrf") != "TENANT", item["site"], item["device"]))[0]
    return {
        "device": match.get("device"),
        "role": match.get("role"),
        "site": match.get("site"),
        "vlan": str(match.get("interface", "")).split(",", maxsplit=1)[0],
        "port": match.get("interface"),
        "location_type": "arp",
        "evidence": "arp",
    }


def _build_path_steps(source: dict[str, object], destination: dict[str, object]) -> list[dict[str, object]]:
    if not source or not destination:
        return [{"type": "unknown", "detail": "source or destination location not found in current MAC table"}]

    steps: list[dict[str, object]] = [
        {
            "type": "source_attachment",
            "device": source.get("device"),
            "role": source.get("role"),
            "site": source.get("site"),
            "interface": source.get("port"),
            "vlan": source.get("vlan"),
        }
    ]

    if source.get("device") == destination.get("device"):
        steps.append(
            {
                "type": "same_device_switching",
                "device": source.get("device"),
                "detail": "source and destination MACs are local on the same device",
            }
        )
    else:
        lldp_path = _shortest_lldp_path(str(source.get("device", "")), str(destination.get("device", "")))
        if lldp_path:
            for hop in lldp_path:
                steps.append({"type": "underlay_hop", **hop})
        else:
            steps.append(
                {
                    "type": "overlay_or_unknown",
                    "from": source.get("device"),
                    "to": destination.get("device"),
                    "detail": "no complete LLDP path found; traffic is inferred through EVPN/VXLAN overlay",
                }
            )

    steps.append(
        {
            "type": "destination_attachment",
            "device": destination.get("device"),
            "role": destination.get("role"),
            "site": destination.get("site"),
            "interface": destination.get("port"),
            "vlan": destination.get("vlan"),
        }
    )
    return steps


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
    const REFRESH_MS = 30000;
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
    for result in sorted(collector.latest_results.values(), key=lambda item: item.device.name):
        for iface in result.interfaces:
            interfaces.append(
                {
                    "device": result.device.name,
                    "interface": iface["name"],
                    "admin_up": bool(iface["admin_up"]),
                    "oper_up": bool(iface["oper_up"]),
                    "rx_bps": float(iface["rx_bps"] or 0),
                    "tx_bps": float(iface["tx_bps"] or 0),
                }
            )
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "interfaces": interfaces,
    }
