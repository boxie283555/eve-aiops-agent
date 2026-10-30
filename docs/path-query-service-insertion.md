# VLAN, VXLAN and service-insertion path queries

Use `/tools?site=lab&src=10.100.101.111&dst=10.200.123.212` or
`/api/path?site=lab&src=10.100.101.111&dst=10.200.123.212`.
The tools page defaults to lab when available. The API defaults to all sites for
compatibility; duplicate address owners require site/device/VRF selection.
Site limits both endpoint resolution and transit devices.

Routed mode evaluates collected forwarding evidence:

- SVI VLAN membership resolves explicit access/trunk ports and aggregate members.
- EOS EVPN route entries retain VTEP, VNI, router MAC and local interface.
  VTEP transit follows the default-VRF route table and confirmed physical links;
  the remote VTEP must own its source interface and map the VNI to a VRF.
- Supported EOS VRF input traffic policies are evaluated in order using original
  source/destination IPs, ingress VLAN and VXLAN decapsulation context. Redirect
  next hops are resolved without changing the original packet destination.
- The traversal state includes ingress context so a one-arm firewall return is
  not mistaken for a routing loop. Physical interfaces and policy/routing steps
  are shown together. Physical topology links and saved positions are unchanged.

Existing running-config collection supplies policy definitions. Only EOS devices
in the current inventory site `lab`, with BGP-sourced traffic-policy fieldsets,
receive the optional `show traffic-policy field-set ipv4 prefix` command during
normal collection. It uses the existing session and command timeout. Failure
marks dynamic evidence unavailable without failing interface/route metrics.
Non-lab collection commands are unchanged. Empty operational sets are distinct
from missing, truncated or unsupported fieldset evidence.

## Interpretation

`ok` means every modeled candidate reaches the destination; `blocked` means all
modeled branches end at a configured discard/drop. Mixed or unresolved branches
remain `incomplete`; ambiguous owners are never chosen arbitrarily. Missing or
stale route, interface, VLAN, VNI, policy or confirmed-link evidence stops the
candidate. Multiple ECMP alternatives are retained within bounded expansion.

Results are configuration/RIB predictions, not packet traces. Firewall security
policy, NAT, ASIC installation and ECMP hashing are not verified. Unsupported
policy predicates/bindings stop evaluation rather than being ignored. Multi-hop
L2 switching, general PBR dialects, IPv6 policy matching and non-default underlay
VRFs are not implemented. Physical mode shows connectivity only, not a traffic
forwarding decision.

## Lab observations, 2026-10-30

Initial read-only diagnostics reported zero prefixes in `bgpDC1` and `bgpDC2`
on both service switches. Replaying those observations predicts
`p1-host → p1-leaf → p1-svc → PA-171 → p1-svc → configured drop`
at `FW-unclassified-DC`; the reverse direction stops on p2-svc.

The post-deployment scheduled collection at 06:41–06:42 UTC reported both sets
populated with the corresponding site host /32 routes. The current forward and
reverse API/page results therefore both return `ok`, with 31 evidence steps:

`p1-host → p1-leaf → p1-svc → PA-171 → p1-svc → p2-svc → PA-172 → p2-svc → p2-leaf → p2-host`

The initial empty-set diagnosis is historical, not the current result. No network
configuration was changed by this application fix. These are forwarding
predictions; no claim of a packet trace or firewall security-policy acceptance
is made.

The lab-only regression fixture preserves the initial condition. A separate,
explicitly synthetic populated-fieldset variant verifies the complete two-firewall
path in both directions; it is not substituted into live results. The final live
API verification independently used the newly collected operational fieldsets.
