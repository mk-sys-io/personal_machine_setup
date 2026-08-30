"""Static configuration: env-driven paths, timing bounds."""
from __future__ import annotations

import os
from pathlib import Path

# Shared credential vault — the single source of truth for API-key creds.
# Distinct from Pi's ~/.pi/agent/auth.json (a deployment target pi_setup
# populates from this vault); they must never share a path.
VAULT_JSON = Path(
    os.environ.get(
        "PI_VAULT_JSON", str(Path.home() / ".config" / "provider-registry" / "vault.json")
    )
)
# Pi extension provider manifest (written by pi-setup auth at the end of the
# copy flow; consumed by the extension's manifest loop).
PI_PROVIDERS_JSON = Path(
    os.environ.get(
        "PI_PROVIDERS_JSON",
        str(Path.home() / ".pi" / "agent" / "extensions" / "live" / "providers.json"),
    )
)

PROBE_TIMEOUT = 15  # seconds per model (fast-only: only responsive models survive)
