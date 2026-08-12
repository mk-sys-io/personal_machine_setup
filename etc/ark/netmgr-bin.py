#!/usr/bin/env python3
"""netmgr — network management CLI for the lockdown system (sys.path launcher).

/usr/local/bin/netmgr points here so the deployed netmgr package (under
$ARK_DATA_PATH/scripts) is importable regardless of the invoking user's
environment — a plain copy of ark/scripts/netmgr.py would fail `from netmgr
import main` because /usr/local/bin is not on the package search path.
"""
import sys

sys.path.insert(0, "{{ .Env.ARK_DATA_PATH }}/scripts")

from netmgr import main

if __name__ == "__main__":
    main()
