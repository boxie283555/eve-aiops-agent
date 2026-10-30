# Device testing scope

- Active validation, troubleshooting sessions, discovery scans, test traffic,
  configuration experiments, and acceptance tests may target only devices whose
  current Nautobot Site (or Location) is exactly `lab`.
- Check the current inventory Site before opening a diagnostic connection. An
  unknown/missing Site is not permission to test. Do not expand a test to peers
  outside `lab`, even when they share IP addresses or MAC addresses.
- Preserve normal scheduled collection for all existing devices. Do not filter
  the production collector to `lab` or change non-lab configuration/credentials.
  Existing aggregate collection health may be inspected passively.
- Offline tests should use synthetic data or lab fixtures, not captures from
  non-lab equipment. Do not refresh old non-lab captures.
- PA lab cabling was confirmed by the user. Corroborate using scoped IP/ARP,
  VLAN, MAC/FDB, and aggregate member evidence; store confirmed physical ports
  in Nautobot. Matching duplicate IP/MAC alone does not establish a cable.
- Preserve saved topology positions and manual page refresh behavior.
