# Language mechanics

The interface rules are in `conventions.md`; only the mechanisms differ.

| Concern | Python | bash |
| --- | --- | --- |
| non-TTY guard | `sys.stdin.isatty()` | `[ -t 0 ]` |
| parser | `argparse`, `parents=[common]` | `while [ $# -gt 0 ]; do case $1 in …` (long flags); `getopts` short-only |
| exit | `return int` from `main`, `sys.exit(main())` | explicit `exit N`; `local rc=$?` after a call you must inspect |
| SIGINT | `KeyboardInterrupt` | `trap 'exit 130' INT TERM`, `trap cleanup EXIT` |
| prompt | `input()` + `EOFError` | `read -r` + `|| rc=$?`; never `read -p` under `sh` |
| JSON | `json.dumps` | `jq -n` if present, else a pre-serialized `printf` |
| colour | `isatty` + `NO_COLOR` | `[ -t 1 ]` + `${NO_COLOR:-}` |
| secrets | `getpass` | `read -rs` |
| lint | `python-lint` | `shellcheck` |
| probes | `grep -n 'add_parser\|input(' tools/*.py` | `grep -n '\[ -t 0 \]\|trap\|read -r' tools/*.sh` |

Full static probes — Python: `grep -n 'add_parser\|input(\|json.dumps\|return [0-9]\|XDG_STATE_HOME\|NO_COLOR' tools/*.py`;
bash: `grep -n '\[ -t 0 \]\|trap\|read -r\|printf' tools/*.sh`;
both: `grep -rn '\[y/N\]' tools lib`.

## Gotchas

- `set -e` stops being applied inside a function used as a conditional (BashFAQ/105); capture the status and handle it explicitly.
- Under `set -u`, an empty `"$@"` is an error on bash < 4.4; the skeleton guards the arg loop with `[ $# -gt 0 ]`.
- `printf '%s\n'` for data; `echo` is for humans.

Source: https://mywiki.wooledge.org/BashFAQ/105
