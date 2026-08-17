#!/usr/bin/env python3
"""netmgr — network management CLI for the lockdown system (sys.path launcher).

/usr/local/bin/netmgr points here so the deployed netmgr package (under
$ARK_DATA_PATH/scripts) is importable regardless of the invoking user's
environment — a plain copy of ark/scripts/netmgr.py would fail `from netmgr
import main` because /usr/local/bin is not on the package search path.

Exit-code contract for `namespace run` (the shim-feeding path): run_cmd uses
exec semantics, so a successfully launched app's exit code passes through
untouched. Any exception here therefore means the launch FAILED — map it to
127 (bash's command-not-found code) so generated thin shims can distinguish a
launch failure from a normal app exit and fall back to the raw binary. All
other subcommands keep Python's default traceback + exit 1.
"""
import sys
import traceback

sys.path.insert(0, "{{ .Env.ARK_DATA_PATH }}/scripts")

_RUN_CMD = sys.argv[1:3] == ["namespace", "run"]

try:
    from netmgr import main
except Exception:  # noqa: BLE001 — any import failure (incl. perms) = launch failure
    traceback.print_exc()
    sys.exit(127)

try:
    main()
except SystemExit:
    raise
except Exception:  # noqa: BLE001
    if _RUN_CMD:
        traceback.print_exc()
        sys.exit(127)
    raise
