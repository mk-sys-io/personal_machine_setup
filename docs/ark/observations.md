# Ark observations — 2026-10-26

Status: open · Scope: allowlist, thin shims, `inet`, `netmgr namespace exec`, PATH.
Method: code inspection + live checks (2026-10-26: `unrestricted`, service
active, no grants). Correction: shell `"$@"` is correct; `netmgr`'s Typer/Click
parser consumes app flags first.

## Summary

| ID | Severity | Area | Finding |
|---|---|---|---|
| OBS-01 | High | CLI | Typer consumes app flags; `opencode stats --days …` fails |
| OBS-02 | High | Exit codes | Nonzero app exits become 127, triggering raw fallback and double execution |
| OBS-03 | High | Resolution | Bare names are not resolved via PATH; `inet opencode …` is denied |
| OBS-04 | Medium | Policy | Revoking a grant removes the shim but not the raw binary's PATH access |
| OBS-05 | Low | Shim | First arg `--ark-logs` is hijacked by every shim |
| OBS-06 | Medium | Deploy | Missing or non-executable grants still deploy shims |
| OBS-07 | Medium | Security | Extra allowlist tokens are ignored; `/usr/bin/podman pull` grants all `podman` |
| OBS-08 | Medium | Environment | Two `sudo` layers reset XDG, Wayland, D-Bus, and API-key env |
| OBS-09 | Medium | Docs | Docs and comments disagree with code on several interfaces |
| OBS-10 | Medium | UX | `remove-grant` lacks the “sync to source” hint that `add-grant` prints |
| OBS-11 | Low | Hardening | Basename collisions, non-atomic shim writes, external `sed` per run |

## OBS-01 — App flags are parsed as netmgr options (High)
- **Evidence:** `netmgr namespace run …/opencode stats --days 2 --models 5` and
  `netmgr namespace exec /bin/echo stats --days 2` both return
  `Error: No such option: --days` (exit 2).
- **Cause:** `render_thin` forwards `"$@"` (`wrappers.py:128`), but `INET_SHIM`
  calls `run "$@"` (`wrappers.py:27`) and `run_cmd` calls `exec` without `--`
  (`namespace.py:142`). Typer parses the first layer; even if `run` receives
  `--`, the inner `exec` rejects it.
- **Impact:** the granted shim fails on any app flag, exiting 2 before the
  126/127 fallback (`wrappers.py:130-133`).
- **Direction:** insert `--` before `cmd` in both layers; add a flag test.

## OBS-02 — Nonzero exits become 127 and double-run the app (High)
- **Evidence (code-traced; no grants configured):** `exec_cmd` and `run_cmd`
  use `subprocess.run(..., check=True)` (`namespace.py:112,142`);
  `/usr/local/bin/netmgr:29-36` maps any `namespace run` exception to 127;
  `render_thin` treats 127/126 in non-locked mode as launch failure and execs
  the raw binary (`wrappers.py:130-133`).
- **Impact:** a launched app that exits nonzero raises `CalledProcessError`,
  becomes 127, and re-runs outside the namespace. Exit codes are not passed
  through; policy or `sudo` failures also trigger the raw fallback.
- **Direction:** pass through the child exit (`os.execvp`/`sys.exit(rc)`);
  reserve 127 for launch failure and use a distinct fallback signal.

## OBS-03 — Bare names are not resolved via PATH (High)
- **Evidence:** `_validate_command` realpaths `cmd[0]` only when it exists
  (`namespace.py:506-514`); bare `opencode` never matches the full-path grant.
  `resolve_binary` already resolves via `shutil.which` (`wrappers.py:78-99`)
  but is unused here.
- **Impact:** `inet opencode stats …` is denied even when
  `/home/mike/.opencode/bin/opencode` is granted, contradicting “Prefer
  `inet`”. Generated shims pass a realpath, so they are unaffected.
- **Direction:** resolve `cmd[0]` with `shutil.which`/`resolve_binary`, or
  require full paths in the interface and docs.

## OBS-04 — Revoking a grant does not disable the tool (Medium)
- **Evidence:** `dotfiles/bashrc:42-44` keeps `~/.opencode/bin` on PATH;
  revocation deletes the shim (`wrappers.py:168-177`) but not the raw binary.
  With the service down and mode not `locked`, `run_cmd` runs any command
  without allowlist validation (`namespace.py:145-153`); `locked` offers an
  interactive bypass (`namespace.py:157-168`).
- **Impact:** with a grant, `/usr/local/bin` wins; without one, the raw binary
  runs outside the netns. Revocation removes namespace access, not execution.
  In `unrestricted` that means normal internet; in `focused` only host
  controls remain. The allowlist is an entrance gate, not an execution control.
- **Direction:** document shim-vs-raw semantics; to disable on revoke, use a
  deny stub or remove the binary.

## OBS-05 — `--ark-logs` is a reserved first argument (Low)
- **Evidence:** `render_thin` intercepts `$1 == --ark-logs`
  (`wrappers.py:123-126`); `INET_SHIM` does the same (`wrappers.py:27`).
- **Impact:** an app whose first argument is `--ark-logs` prints the netmgr
  log path and exits 0 instead of running.
- **Direction:** namespace the flag (`--netmgr-logs`), use an env var, or
  require `--` before app args.

## OBS-06 — Invalid grants still deploy shims (Medium)
- **Evidence:** `read_grants` marks missing paths (`wrappers.py:64-71`), but
  `deploy` never checks `g.missing`; it adds the name to `current` and writes
  the shim (`wrappers.py:151-158`). `list_grants` shows `MISSING`
  (`namespace.py:187`).
- **Impact:** uninstalling a granted binary leaves a dead shim on PATH; the
  name is in `current`, so stale-shim sweeping does not remove it.
- **Direction:** skip or fail missing/non-executable grants and remove shims.

## OBS-07 — Extra allowlist tokens silently broaden a grant (Medium)
- **Evidence:** both readers take only `line.split()[0]` (`wrappers.py:64`,
  `namespace.py:513-514`); `docs/ark/ark-user-guide.md:245,249` still advertises
  `[arg]`.
- **Impact:** a stale `/usr/bin/podman pull` line grants all `podman`
  subcommands, not just `pull` — silent security broadening.
- **Direction:** reject trailing tokens or implement exact matching; migrate
  the docs and existing entries.

## OBS-08 — Environment is reset across two sudo layers (Medium)
- **Evidence:** `exec_cmd` runs `ip netns exec … sudo -u user …`
  (`namespace.py:111-112`). `etc/ark/sudoers.d/40-netmgr` has only NOPASSWD;
  `00-base` defines no `env_keep`/`Defaults`. `docs/restriction-policy.md:357-359`
  already says GUI env vars must be explicitly preserved.
- **Impact:** `XDG_RUNTIME_DIR`, `WAYLAND_DISPLAY`, `DBUS_SESSION_BUS_ADDRESS`,
  `SSH_AUTH_SOCK`, and API keys are dropped. Terminal tools may be unaffected;
  GUI tools and agents needing them break.
- **Direction:** preserve an explicit env list or document the limitation.

## OBS-09 — Docs and comments disagree with code (Medium)
- **Evidence:** `docs/ark/ark-user-guide.md:174-175` says `"$@"` passes through
  (OBS-01); `:183` says “Prefer `inet`” (OBS-01/03); `:245,249` document
  `[arg]` (OBS-07); `:251-252` documents `deploy [--bashrc <path>]` and a
  “generated alias block” that do not exist. `__init__.py:432` says
  “deploy/list any mode” while `wrappers.py:148` requires unrestricted;
  `enable.py:353` calls deploy “ungated” (ordering works, comment misleading).
  `dotfiles/bashrc:42` still says uncomment only if the shim is unavailable,
  though line 44 is uncommented.
- **Impact:** users follow interfaces that fail; future changes inherit false
  assumptions.
- **Direction:** reconcile docs/comments with code; add a parity check.

## OBS-10 — `remove-grant` does not remind you to sync (Medium)
- **Evidence:** `add_grant` prints the sync line (`namespace.py:282`);
  `remove_grant` only logs “Revoked” (`namespace.py:313-319`).
- **Impact:** a live removal silently reverts on the next deploy that
  re-renders the source allowlist.
- **Direction:** print the same sync hint on remove.

## OBS-11 — Shim deployment edge cases (Low)
- **Evidence:** shims are keyed by basename (`wrappers.py:154-156`), so
  duplicate basenames overwrite. `_write_shim` uses `write_text` then
  `chmod`/`chown` (`wrappers.py:161-165`), which is not atomic. Every shim
  calls external `sed` to read mode (`wrappers.py:127`).
- **Impact:** duplicate basenames route to one binary; an interrupted write
  can leave a partial shim; missing `sed` or unusual PATH leaves mode empty
  and disables fallback.
- **Direction:** detect duplicate basenames, write via temp + rename, read
  mode with a bash builtin.

## Corrected assumptions
- Shell `"$@"` plus `exec` is correct; OBS-01 is a CLI-parser failure above it.
- Bare-binary grants intentionally allow all subcommands; OBS-07 covers
  ignored trailing tokens, not that design.
- `sudo` env reset is partly documented; the gap is missing preservation of
  the variables the policy names.

## Cross-references
- `docs/ark/ark-user-guide.md` — user-facing behavior and command examples.
- `docs/restriction-policy.md` — policy, threat model, env checklist.
- `docs/ark/ark-enhancement.md` — design context.
- `docs/adr/README.md` — file an ADR only if a fix crosses the gate.
- `docs/backlog.md` — park individual fixes after triage; do not duplicate.
