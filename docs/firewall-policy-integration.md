# Firewall Policy Center v1

The workspace at `/firewall/?lang=zh` (or `lang=en`) integrates the policy
inventory and analysis from `boxie283555/FireWall-Policy-tools` into AIOps.
The original local application and its database are unchanged. Initial online
scope is the existing Nautobot lab firewalls PA-171 and PA-172; no duplicate
Nautobot device or independent management credential store is created.

## Architecture and data

- A stateless `firewall-policy` service parses/analyzes policy evidence. It has
  no host port, device access, discovery or persistent database. It uses a
  separate image, CPU/memory limits, read-only filesystem and two workers.
- AIOps exposes the page and `/api/firewall/*` gateway, checking current lab
  membership and UUID/host/platform identity against Q3 backup evidence.
- Q3 raw and model hashes are checked. Only redacted policy/object subtrees
  reach the internal analysis service. Browsers receive scoped rules/objects,
  evidence timestamps, risks and redacted differences; not full raw backups.
- Inventory uses saved running-configuration backups, not candidate state or
  the original tool's manually imported copy. Refresh reads saved evidence;
  new observations come from Q3 daily/manual backups. Age and version are
  visible. Unavailable analysis is an error, not a healthy empty result.
- Successful analysis is cached by device, name, version and sanitized content
  digest. At most two analysis requests run in the gateway; failures do not
  block scheduled device collection or overwrite backup evidence.

## Identity and analysis

Every rule is scoped by Nautobot UUID, VSYS and rulebase. PA native rule UUID
is preferred; when absent, the scoped rule name is used (rename then creates
new identity). Order and backup ID are separate fields. Shared/local objects
are resolved with local overrides in each VSYS. Pairwise findings cannot
cross contexts or devices. Address ranges/networks/static groups and explicit
TCP/UDP service ranges participate in coverage checks; source-user is included.
Negation and other unsupported conditions, unresolved objects and
application-default are excluded from definitive coverage claims. Pairwise
checks above 450 enabled rules are explicitly skipped. Original heuristic
findings remain advisory, including broad access and missing owner metadata.

The first release provides security rules, NAT rules, objects, search, CSV,
risk details, change previews and persistent review reports. NAT inventory
shows configured translation; it does not claim observed dynamic allocations.
The upstream multi-vendor parser is retained internally, but public inventory
and device execution in this release are PA-only. ASA/FortiGate/Huawei online
management is not advertised or enabled without vendor-specific validation.

## Change impact and reports

Supported proposals: enable/disable, security action, replace source/destination/
service values, or move before/after another rule in the same rulebase. The
selected backup is copied in memory; profile settings and other existing
fields are retained. Empty replacements and no-op changes are rejected.

A preview records its immutable rule/version/operation, actor declaration,
reason, redacted diff, introduced/resolved risks and optional A→B comparison.
`firewall_policy_preview` enters the Q3 operation audit. Review records live in
`firewall_reviews` in the existing configuration-history database. HTML reports
are readable/printable; JSON preserves the full evidence. The status is
`previewed`, never inferred approval or execution.

The optional flow uses AIOps `find_path` and its existing PA policy/NAT engine.
Both before/after use the same in-memory topology snapshot and other-firewall
policy evidence. The selected firewall's backup/candidate policy is substituted
only within the calculation. Reports state the mixed time basis explicitly:
selected backup on current topology. Unresolved routes, policy semantics or
stale interfaces remain incomplete. Results cover only the supplied new
connection and do not prove all affected applications or packet delivery.

## Opt-in controlled PA execution

Writes are disabled by default. Two explicit server settings are necessary:
`AIOPS_FIREWALL_WRITE_DEVICES` contains exact Nautobot device UUIDs and
`AIOPS_FIREWALL_OPERATOR_TOKEN` contains a separately managed operator secret.
Do not put that secret in source control, URLs, report downloads or browser
storage. The UI exposes execution only for an enabled device and sends the
secret in `X-Firewall-Operator-Token`, clearing the input after use. This is an
operator-token boundary, not an enterprise SSO/approval system.

Execution requires an unexpired 10-minute preview, exact device-name
confirmation and explicit full-commit confirmation. It runs as a bounded
background task, rechecks current Nautobot lab scope, refuses existing/unreadable
config/commit locks, acquires its own locks, refuses any pending candidate
changes, and requires current running config to equal the reviewed backup.
A generated Q3 change reference is registered before the baseline observation.
The single reviewed rule operation is written once; the entire candidate must
match the reviewed target and running must remain unchanged before full commit.
Only a FIN/OK job and verified running fingerprint produce
`verified_configuration`; business traffic validation remains separate.

Timeouts are never automatically retried. Any uncertain outcome after a write,
lock-release failure, or interrupted job becomes `inspection_required`, blocking
further writes for that device. The task does not automatically revert candidate
or running configuration. Inspect device locks/candidate/commit jobs and recover
through an explicitly reviewed procedure. Agent restart marks queued/running
jobs for inspection. Pre/post backups and drift attribution use the existing
Q3 store, while ordinary collection remains independent.

This release's execution path is covered by synthetic device-API tests;
actual PA write/commit/rollback acceptance is not claimed. Deployment leaves
both physical firewall writes disabled and performs only offline previews.

## Deployment and verification

Build/start `firewall-policy`, then rebuild/recreate only `agent`. Existing
Prometheus, Grafana, Nautobot and Telegraf services are preserved. Four Grafana
views, Configuration & Drift and Tools link to the new workspace. Source,
agent image, consistent configuration-history SQLite backup and saved topology
layout are captured before deployment.

Checks cover identity stability, context isolation, address/user coverage,
secret filtering, backup tampering, offline preview/audit, commit gates,
pending-change refusal, single-use reviews, timeout handling, concurrent candidate
rejection, and Q3 drift ownership. Browser checks exercise both languages,
versions/devices, inventory/risk, reports and narrow-screen layouts. Live
acceptance uses existing lab backup evidence without device configuration writes.

### Live acceptance — 2026-11-02

- Deployed workspace: `http://192.168.20.169:8080/firewall/?lang=zh`.
- Existing Nautobot identities confirmed: PA-171
  `889f445d-b67a-4953-afd3-2de05eb17416`, PA-172
  `2e91eaa3-792a-4855-93bb-a9cee52aabd5`. Each saved configuration contains
  2 security rules, 4 objects and no NAT rules. Device writes remain disabled.
- 103 automated tests passed, including existing path/configuration regressions.
  Synthetic and live browser checks passed for policy inventory, offline reviews,
  Chinese/English and mobile layout. Existing four Configuration & Drift tabs
  passed their live navigation/language/mobile regression without writes.
- Offline review `d294819174744b2aaed05da4a39ad8bb` models changing PA-171
  `vlan101-vlan123-1` to deny for TCP
  `10.100.101.111:40000 → 10.200.123.212:50001`: before `ok`, after `blocked`
  at PA-171. The printable report includes both device chains. A subsequent
  normal live path query remained `ok`; no device configuration was changed.
- Lab collection: 10/10 successful. Nautobot interface sync: `ok`, 941 updated
  interfaces, no failed devices. The existing Nautobot container reports
  `unhealthy` despite successful application synchronization; container health
  diagnostics were outside this integration acceptance.
- Existing 25 configuration versions, 15 drift records and saved topology
  layout matched the predeployment audit. Only offline review/audit records
  were added by the preview checks.
- Deployment recovery bundle:
  `/opt/eve-aiops-backups/firewall-integration-20261102-064259`.
- Actual PA commit/rollback acceptance, broader vendor validation and use of
  reports in a real change review remain outstanding; this deployment alone
  does not complete Q4 acceptance.
