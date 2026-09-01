"""Static configuration: env-driven paths, timing bounds."""
from __future__ import annotations

import os

# gopass entry prefix for provider API keys (structured secret, key field).
# Entry layout: <prefix>/<provider-id> with key + strategy fields.
GOPASS_ENTRY_PREFIX = os.environ.get("PI_GOPASS_PREFIX", "provider-registry")
# gopass binary name/path (overridable for dev/test).
GOPASS_BIN = os.environ.get("PI_GOPASS_BIN", "gopass")
