# Operational parser fixtures

Captured on 2026-10-30 from the authorized EVE lab. These files contain only interface and routing command output; no credentials or running configuration.

- `q2-PA-171.json` / `q2-PA-172.json`: PAN-OS aggregate membership, VR-EW subinterfaces, identical endpoint addresses, and active route flags.
- `q2-p1-svc.json`: EOS routes grouped by VRF.
- `q2-raw-DUT-*.txt`: EOS JSON followed by a prompt containing a changing clock. Keep the trailing prompt: it reproduces the production parsing failure.
