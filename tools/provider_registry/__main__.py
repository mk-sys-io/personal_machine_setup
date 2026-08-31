"""Entry point: python3 -m tools.provider_registry (dev) or the deployed zipapp."""
from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
