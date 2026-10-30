# Operational parser fixtures

Lab fixtures captured on 2026-10-30. These files contain only interface and routing command output; no credentials or running configuration. Future tests must use lab or synthetic data, per the repository AGENTS.md.

- `q2-PA-171.json` / `q2-PA-172.json`: PAN-OS aggregate membership, VR-EW subinterfaces, identical endpoint addresses, and active route flags.
- `q2-p1-svc.json`: EOS routes grouped by VRF.
- `q2-raw-DUT-*.txt`: historical non-lab captures, no longer loaded by tests. Do not refresh or use these for future validation. Clock-prompt coverage now uses synthetic JSON and prompts in `test_normalized_model.py`.
