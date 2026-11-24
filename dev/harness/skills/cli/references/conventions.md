# CLI checks

Verifiable from source or `--help`. `(run)` and `(handoff)` rows execute only
for a tool created in this session or when the user authorizes it. One question
sets the tier (see `SKILL.md`): I = internal, S = shared, P = published. A rule
outside the tool's tier is not a finding.

## Interface matrix

| Check | Tier | Expected |
| --- | --- | --- |
| `-h`, `--help`; each subcommand `-h` | I S P | exit 0, usage on stdout; one-line `help=` in parent |
| unknown flag or subcommand (run) | I S P | exit 2, usage on stderr |
| `--version` | S P | string, exit 0. `I`: optional, and only if derived (`git describe`) — never hand-maintained |
| help length | I S P | one screen, one example |
| every flag has a long form; `-f`, `--flag`, `--flag=value`, `--` | I S P | yes |
| subcommand naming; hidden commands | I S P | one scheme, kebab-case; none hidden |
| required args | I S P | visible in help; none on the common path |
| config precedence | S P | flags > env > project > user > defaults, documented. `I`: flags + one env var |
| rename or repurpose a flag | P | alias + deprecation, never two meanings. `I`: rename, grep the callers |
| success / failure / usage (run) | I S P | 0 / 1 / 2 only |
| SIGINT (handoff) | I S P | 130, cleanup done |
| error path returning 0 | I S P | none |
| data vs messaging | I S P | data stdout only; logs, progress, prompts, errors stderr |
| `--json` | S P | present when a program consumes output, one document on stdout. `I`: only if consumed |
| JSON errors | S P | JSON on stderr plus nonzero exit |
| colour | S P | off when piped; honors `NO_COLOR`, `TERM=dumb`. `I`: only if it emits colour |
| piped stdout (run) | S P | no ANSI, no spinner, no truncation surprises |
| prompt text / accepted answers | I S P | `[y/N]`, Enter aborts, `y` and `yes` |
| stdin not a TTY (run) | I S P | no prompt; exit 2 with the re-run line |
| non-interactive override | I S P | `--yes` is the documented path; an env var is a CI convenience only |
| `--no-input` | S P | fails instead of prompting |
| TTY guard | I S P | `isatty` / `[ -t 0 ]`, EOF handled |
| error shape, prefix, concrete paths | I S P | what failed, why, how to fix; `error:`/`warning:` |
| stack traces | S P | only behind `--debug` |
| mutating verb; destructive confirmation; `--dry-run` | I S P | explicit name; confirm or `--yes`, refuse without TTY; real code path, zero writes |
| rerun | I S P | second run is a no-op, not an error |
| secrets | I S P | never in argv; see `docs/secrets.md` |
| state paths | I S P | `$XDG_STATE_HOME` honoured when set |
| completion, man page, feedback URL, license | P | present |
| telemetry | P | consent required |

## Severity by tier

- Report the tier first; a rule outside it is not a finding.
- `P`-only omissions on a `published` tool are MEDIUM; at `I` they are absent.
- CRITICAL keeps its bar at every tier, as defined in `SKILL.md`.

## Hand-off probes (operator runs)

```text
tool --help; tool <sub> -h; tool --version; tool nope
tool list | cat
tool list > /dev/null
tool mutate < /dev/null
echo $?
```
