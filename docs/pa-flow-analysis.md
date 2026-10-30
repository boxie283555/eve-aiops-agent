# Flow state and PAN-OS policy/NAT analysis v1

The tools page has two analysis modes. **Routing** preserves the existing API
behavior. **Flow** evaluates a new connection using the specified protocol,
source/destination ports and optional assumed App-ID. No application or port is
invented when inputs are omitted. Explicit source ingress is optional; an owned
source address otherwise means traffic originated by that device. A learned
endpoint starts at its observed ingress attachment.

Example, current lab:

```text
/tools?site=lab&src=10.100.101.111&dst=10.200.123.212&analysis=flow&protocol=tcp&source_port=40000&destination_port=50001
/api/path?site=lab&src=10.100.101.111&dst=10.200.123.212&analysis=flow&protocol=tcp&source_port=40000&destination_port=50001
```

Both endpoints share the same engine. New optional parameters are `analysis`
(`routing` default / `flow`), `protocol` (`any`, `tcp`, `udp`, `icmp`, `icmpv6`),
`source_port`, `destination_port`, `application`, and `ingress_interface`.
`flow_model_version=1.0`, the original `flow`, per-hop `flow_before/flow_after`,
and each branch's `final_flow` are additive to topology schema 1.1.

Immutable packet state travels independently through each branch. EOS policies
see the current tuple after upstream translation. The PA pipeline uses:

1. Ingress logical interface → VSYS/zone.
2. Original destination route → original destination zone for NAT selection.
3. First matching NAT rule and deterministic address/port translation.
4. Translated destination route → destination zone for security selection.
5. First matching security rule, evaluated against original addresses/ports and
   the post-DNAT destination zone. Only an allow advances the packet.
6. Continue the existing route/physical/VXLAN traversal using the new tuple.

Supported evidence includes local running security/NAT rules, enabled state,
rule ordering, source/destination negation, address/netmask/range objects,
recursive static address/service groups, TCP/UDP services including source
ports, zone/rule type, default-rule actions, deterministic static SNAT and
single-address DNAT with destination-port translation. Known profiles/logging
configuration does not change the modeled rule decision; threat-profile
acceptance is not claimed.

Unknown conditions preserve ordering. An earlier possibly matching rule stops
evaluation; it is never skipped to reach a later allow. Definitely nonmatching
rules may be skipped even when an unrelated predicate is unknown. Unsupported
conditions include dynamic address groups/FQDN resolution, schedules/user/HIP,
application-default without an application service database, Panorama effective
policy, enabled PA PBF, IPv6 PA policy, bidirectional static-NAT implicit rules,
and NAT pools/PAT allocation. Dynamic SNAT/PAT currently stops with an explicit
request for observed translation evidence; it is not assigned a fabricated
address or source port. Supplied App-ID is an assumption, not an observation.

## Background collection

A separate scheduler job runs every 300 seconds with at most two PA workers.
It verifies current Nautobot site `lab`, device UUID, platform and host before
opening management API connections. It reads **running** configuration using
XML API `type=config, action=show`; candidate configuration is not used. API
calls have bounded timeouts and response sizes. No change/commit commands exist
in this integration. Existing metrics collection and interface sync are not
blocked by this job, and non-lab collection commands are unchanged.

Only normalized policy data is written to `/app/data/panos-policy.json`
(overridable with `AIOPS_PANOS_POLICY_PATH`). Raw device configuration, API keys
and credentials are not persisted. The existing lab self-signed-certificate
handling is used. Collection failures immediately invalidate policy evidence;
original success timestamps are retained. Queries also apply the existing
freshness TTL (normally 600 seconds). Status is available at
`GET /api/pa/policy-status`.

## Native verification and sessions

Click a PA visit in the path diagram and choose **PA 原生规则/会话核验**.
`POST /api/pa/verify` performs read-only security-policy-match,
nat-policy-match and exact-flow session queries. This is separate from both
routing requests and ordinary metrics collection, bounded to two concurrent
requests, and requires current Nautobot lab membership. The request includes
the inspected tuple, logical ingress, VSYS, original/final zones and the policy
snapshot digest. Changed/expired cache evidence requires a new path query.
TCP/UDP native checks require both ports to avoid broad session searches.

Native rule matches are labeled device-query evidence. Session entries are
shown separately; no matching session means **not observed**, not denied.
Neither result certifies all packets reached the destination. Native responses
do not silently override a configuration prediction or supply guessed NAT
allocations. Some PAN-OS versions return an error `No nat-policy` when no NAT
policy exists; only that exact observed response is normalized to `no_match`.
Other errors remain unavailable.

Reverse queries describe independent **new connections**. They do not simulate
return packets of an existing stateful session, invert NAT automatically, or
bypass the reverse security policy on an assumed session. Stateful reverse
translation and dynamic PAT correlation remain a later extension.

## Verification scope

Regression tests use synthetic NAT/deny cases and a sanitized lab-only PA
policy fixture. They cover original versus translated destination/zone/port,
continued routing after DNAT, immutable flow state, first-match uncertainty,
blocked paths, unsupported behavior, stale policy cache, API validation and
compatibility with existing routing queries. No lab NAT/deny policy is created
solely for acceptance testing. Existing lab TCP/50001 allow rules provide the
live two-firewall check; the actual devices currently have no NAT policies.

Deployment acceptance on 2026-10-30 used
`10.100.101.111:40000 → 10.200.123.212:50001/TCP`. Both PA-171 and PA-172
matched `vlan101-vlan123-1` with action `allow`, in both the configuration
engine and native device queries. Native NAT queries returned `no_match`,
and exact-flow session queries returned `not_observed`; packet delivery is
therefore not proven. The live browser displayed 10 device visits and both
native verification buttons completed without JavaScript errors. The legacy
routing query retained 31 evidence steps; flow analysis returned 43 steps.
TCP/443 stopped as incomplete at the `application-default` rule, as intended.
The local regression suite passed all 115 tests.
