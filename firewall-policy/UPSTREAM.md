# Firewall Policy Tools integration

The parser, models and heuristic rule checks in `policy_service/upstream/` come
from the user's repository https://github.com/boxie283555/FireWall-Policy-tools
at commit dc81f4ab4293ccb6545c037b16fe54fe1b983d7c.

Import paths are adapted and the parser's secondary topology extraction is
disabled. `analysis.py` supplies device/context isolation, stable identities,
object-aware coverage, conservative unsupported-condition handling and a
versioned analysis envelope. The original application/database is unchanged.

This internal, stateless service cannot connect to network devices, write
configuration, or discover equipment. AIOps supplies scoped policy evidence.
No host port or raw-configuration API is exposed to browsers.
