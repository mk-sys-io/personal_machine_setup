# CLI checks

Verifiable from source or `--help`. `run` and `handoff` execute only for a tool
created in this session or when the user authorizes it.

## Help and version

| Check | Expected | How |
| --- | --- | --- |
| `-h`, `--help` | exit 0, usage on stdout | help |
| each subcommand `-h` | exit 0, one-line `help=` in parent | help |
| `--version` | version string, exit 0 | help |
| unknown flag or subcommand | exit 2, usage on stderr | run |
| help length | one screen, one example | help |

## Grammar and flags

| Check | Expected | How |
| --- | --- | --- |
| every flag has a long form | yes | static |
| flag syntax | `-f`, `--flag`, `--flag=value`, `--` ends flags | static |
| subcommand naming | one scheme, kebab-case | static |
| hidden commands | none | static |
| required args | visible in help; none on the common path | static |
| config precedence | flags > env > project > user > defaults, documented | static |

## Exit codes

| Check | Expected | How |
| --- | --- | --- |
| success | 0 only | run |
| operational failure | 1 | run |
| usage error | 2 | run |
| SIGINT | 130, cleanup done | handoff (tty) |
| error path returning 0 | none | static |

## Output and streams

| Check | Expected | How |
| --- | --- | --- |
| data | stdout only | static |
| logs, progress, prompts, errors | stderr | static |
| `--json` on listing or reading verbs | present, one document on stdout | help |
| JSON errors | JSON on stderr plus nonzero exit | static |
| color | off when piped, honors `NO_COLOR`, `TERM=dumb` | static |
| piped stdout | no ANSI, no spinner, no truncation surprises | run |

## Prompts and TTY

| Check | Expected | How |
| --- | --- | --- |
| prompt text | `[y/N]`, Enter aborts | static |
| accepted answers | `y` and `yes` | static |
| stdin not a TTY | no prompt; exit 2 with the re-run line | run |
| `--yes` | skips every confirmation | static |
| `--no-input` | fails instead of prompting | static |
| `input()` | guarded by `isatty`, `EOFError` handled | static |

## Errors

| Check | Expected | How |
| --- | --- | --- |
| shape | what failed, why, how to fix | static |
| prefix | `error:` or `warning:`, lowercase after | static |
| stack traces | only behind `--debug` | static |
| paths and values | concrete, never "permission denied" alone | static |

## Safety and lifecycle

| Check | Expected | How |
| --- | --- | --- |
| mutating verb | explicit name, never the default | static |
| destructive op | confirmation or `--yes`; refuses without a TTY | static |
| `--dry-run` | real code path, zero writes, exit 0 if it would proceed | static |
| rerun | second run is a no-op, not an error | static |
| secrets | never in argv; see `docs/secrets.md` | static |
| state paths | `$XDG_STATE_HOME` honored when set | static |
| versioned surface | flags, exit codes, JSON fields deprecate, never repurpose | static |

## Static probes

```text
grep -n 'input(' tools/*.py lib/*.py     # isatty guard plus EOFError
grep -n 'add_parser' tools/*.py          # help= present
grep -n 'json.dumps' tools/*.py          # sole stdout write on that path
grep -n '^Usage:' tools/*.py             # docstring drift versus argparse
grep -rn '\[y/N\]' tools lib             # accepted tokens include y|yes
grep -n 'return [0-9]' tools/*.py        # literal exit codes
grep -n 'NO_COLOR\|isatty' tools/*.py
grep -n 'XDG_STATE_HOME\|XDG_CACHE_HOME' tools/*.py lib/*.py
```

## Hand-off probes (operator runs)

```text
tool --help; tool <sub> -h; tool --version; tool nope
tool list | cat            # ANSI stripped
tool list > /dev/null      # human output still on stderr
tool mutate < /dev/null    # must not hang
echo $?                    # after success, usage error, failure
```
