"""Parse reported management-plane CPU and memory, never invent hardware sensors."""

import re


def top_environment(output):
    cpu_line = next(
        (line for line in output.splitlines() if re.search(r"cpu\(s\)", line, re.I)), ""
    )
    idle = re.search(r"([0-9.]+)\s*%?\s*id\b", cpu_line)
    memory = re.search(r"^\s*(?:(KiB|MiB|GiB)\s+)?Mem\s*:\s*(.*)$", output, re.M | re.I)
    if not idle or not memory:
        raise ValueError("Device did not report CPU idle and memory totals")
    values = {
        label: float(value)
        for value, label in re.findall(
            r"([0-9.]+)\s*[kKmMgG]?\s+(total|used|free)\b", memory.group(2)
        )
    }
    if not {"total", "used"} <= values.keys():
        raise ValueError("Device did not report total and used memory")
    factor = {None: 1, "kib": 1, "mib": 1024, "gib": 1024 * 1024}[
        memory.group(1).lower() if memory.group(1) else None
    ]
    total, used = int(values["total"] * factor), int(values["used"] * factor)
    idle_value = float(idle.group(1))
    if not 0 <= idle_value <= 100 or not 0 <= used <= total or total == 0:
        raise ValueError("Invalid reported CPU or memory counters")
    return {
        "cpu": {0: {"%usage": round(100 - idle_value, 1)}},
        "memory": {"available_ram": total, "used_ram": used},
        "fans": {},
        "temperature": {},
        "power": {},
    }
