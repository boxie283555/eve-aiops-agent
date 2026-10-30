"""Conservative EOS traffic-policy evidence and ordered IPv4 evaluation.

Unknown predicates never become a match or a miss. An empty operational BGP
fieldset is different from an unavailable fieldset. This models configuration,
not ASIC installation, firewall policy, NAT, or packet delivery.
"""
from __future__ import annotations

import re
from ipaddress import ip_address, ip_network

from .routing import command_ok


def vlan_allowed(port, vlan):
    mode = port.get("switchport_mode")
    if mode == "access":
        return str(vlan) == str(port.get("access_vlan", "1"))
    if mode != "trunk":
        return False
    expression = port.get("allowed_vlans", "all")
    if expression == "all":
        return True
    if expression == "none":
        return False
    try:
        return any(int(a) <= int(vlan) <= int(b) for a, b in (
            (token.split("-", 1) if "-" in token else (token, token))
            for token in expression.split(",")
        ))
    except ValueError:
        return False  # unsupported 'except/add/remove' syntax is not evidence


def operational_fieldsets(output):
    rows = {}
    if not command_ok(output):
        return rows
    current = None
    for line in output.splitlines():
        m = re.match(r"Traffic policy IPv4 prefix field set: (\S+)", line)
        if m:
            current = {"prefixes": [], "exceptions": [], "count": None, "exception_count": None}
            rows[m[1]] = current
            continue
        if current is None:
            continue
        m = re.match(r"Prefix count: (\d+)", line)
        if m:
            current["count"] = int(m[1])
        m = re.match(r"Exception prefix count: (\d+)", line)
        if m:
            current["exception_count"] = int(m[1])
        fields = line.split()
        if fields and re.fullmatch(r"\d+\.\d+\.\d+\.\d+/\d+", fields[0]):
            try:
                current["prefixes"].append(str(ip_network(fields[0], strict=False)))
                current["exceptions"].extend(str(ip_network(v, strict=False)) for v in fields[1:])
            except ValueError:
                current["count"] = None
    return {name: row["prefixes"] for name, row in rows.items()
            if row["count"] == len(row["prefixes"]) and row["exception_count"] == 0
            and not row["exceptions"]}


def eos_forwarding(config, fieldsets=""):
    if not command_ok(config):
        return {"status": "unavailable", "bindings": [], "policies": {}, "fieldsets": {}}
    result = {"status": "ok", "bindings": [], "policies": {}, "fieldsets": {}}
    active = False
    section = name = rule = None
    for line in config.splitlines():
        if line == "traffic-policies":
            active = True
            continue
        if line and not line[0].isspace():
            active = False
        if not active or not line.strip() or line.strip() == "!":
            continue
        text = line.strip()
        indent = len(line) - len(line.lstrip())
        if indent == 3:
            rule = None
            if text.startswith("vrf "):
                section, name = "vrf", text.split()[1]
            elif text.startswith("field-set ipv4 prefix "):
                section, name = "fieldset", text.split()[-1]
                result["fieldsets"][name] = {"source": "static", "prefixes": [], "available": True}
            elif text.startswith("traffic-policy "):
                section, name = "policy", text.split()[1]
                result["policies"][name] = []
            else:
                section = None
        elif section == "vrf":
            m = re.fullmatch(r"traffic-policy input (\S+) physical", text)
            result["bindings"].append({"vrf": name, "policy": m[1] if m else None, "supported": bool(m)})
        elif section == "fieldset":
            row = result["fieldsets"][name]
            if text == "source bgp":
                row.update(source="bgp", prefixes=[], available=False)
            else:
                try:
                    row["prefixes"].extend(str(ip_network(v, strict=False)) for v in text.split())
                except ValueError:
                    row["available"] = False
        elif section == "policy":
            m = re.fullmatch(r"match (\S+) (ipv4|ipv6)", text)
            if m:
                rule = {"name": m[1], "family": m[2], "action": "pass", "unsupported": []}
                result["policies"][name].append(rule)
            elif rule is not None:
                m = re.fullmatch(r"(source|destination) prefix field-set (\S+)", text)
                if m:
                    rule[m[1]] = m[2]
                elif text.startswith("dot1q vlan ") and text.split()[-1].isdigit():
                    rule["vlan"] = text.split()[-1]
                elif text in {"vxlan decap", "vxlan decap exclude"}:
                    rule["decap"] = text == "vxlan decap"
                elif re.fullmatch(r"redirect next-hop (\S+) vrf (\S+)", text):
                    rule.update(action="redirect", next_hop=text.split()[2], vrf=text.split()[4])
                elif text == "drop":
                    rule["action"] = "drop"
                elif text not in {"actions", "count", "packet type"}:
                    rule["unsupported"].append(text)
    runtime = operational_fieldsets(fieldsets)
    for name, row in result["fieldsets"].items():
        if row["source"] == "bgp" and name in runtime:
            row.update(prefixes=runtime[name], available=True)
    # Interface-bound policies are outside this VRF binding evaluator.
    if re.search(r"^\s+(?:traffic-policy input|service-policy type pbr|ip policy route-map) ", config, re.M):
        # VRF binding lines supported above have exactly six leading spaces.
        result["unsupported_bindings"] = any(
            len(line) - len(line.lstrip()) != 6 or not line.strip().endswith(" physical")
            for line in config.splitlines() if re.match(r"\s+(?:traffic-policy input|service-policy type pbr|ip policy route-map) ", line)
        )
    return result


def evaluate_policy(evidence, vrf, src, dst, vlan=None, decap=False):
    """Return (selected rule with evidence, uncertainty). None rule means no binding."""
    if evidence is None:  # older/synthetic snapshots retain the documented RIB-only contract
        return None, None
    if evidence.get("status") != "ok" or evidence.get("unsupported_bindings"):
        return None, "Traffic-policy evidence is missing or uses unsupported bindings"
    bindings = [b for b in evidence.get("bindings", []) if b["vrf"] == vrf]
    if not bindings:
        return None, None
    if len(bindings) != 1 or not bindings[0].get("supported"):
        return None, "Unsupported or multiple traffic-policy bindings"
    policy = bindings[0]["policy"]
    if policy not in evidence["policies"]:
        return None, "Bound traffic-policy definition is missing"
    try:
        if ip_address(dst).version != 4 or (src and ip_address(src).version != 4):
            return None, "Only IPv4 traffic-policy evaluation is supported"
    except ValueError:
        return None, "Policy endpoint address is invalid"
    empty_sets = []
    for rule in evidence["policies"][policy]:
        if rule["family"] != "ipv4":
            continue
        if "decap" in rule and rule["decap"] != decap:
            continue
        if "vlan" in rule and vlan is not None and str(vlan) != rule["vlan"]:
            continue
        unknown = list(rule["unsupported"])
        if "vlan" in rule and vlan is None:
            unknown.append("ingress VLAN")
        misses = False
        sets = {}
        for key, address in (("source", src), ("destination", dst)):
            if key not in rule:
                continue
            name = rule[key]
            fs = evidence["fieldsets"].get(name, {})
            sets[name] = fs
            if not fs.get("available") or not address:
                unknown.append(name)
            else:
                if not fs["prefixes"] and fs.get("source") == "bgp" and name not in empty_sets:
                    empty_sets.append(name)
                try:
                    if not any(ip_address(address) in ip_network(p) for p in fs["prefixes"]):
                        misses = True
                except ValueError:
                    unknown.append(name)
        if misses:
            continue
        if unknown:
            return None, f"Policy {policy}/{rule['name']} cannot be evaluated: {', '.join(unknown)}"
        return {**rule, "policy": policy, "fieldsets": sets, "ingress_vlan": vlan, "decap": decap, "empty_fieldsets": empty_sets}, None
    return {"policy": policy, "name": "implicit-pass", "action": "pass"}, None
