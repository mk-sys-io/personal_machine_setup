from __future__ import annotations

import os
import sys

if os.geteuid() != 0:
    sys.exit("Error: ark requires root\n  Run: sudo ark <command>")

sys.path.insert(0, "{{ .Env.ARK_DATA_PATH }}/scripts")
from ark.main import main

main()
