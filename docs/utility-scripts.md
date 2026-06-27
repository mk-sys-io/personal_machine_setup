# Utility Scripts — Convention & Deployment

## Pattern

All user-facing CLI utilities live in `.config/scripts/` and follow:
- `#!/bin/bash` shebang, `set -euo pipefail` (or equivalent rigor)
- Exit 0 on success, 1 on failure (or structured exit codes)
- Informational output to stdout, errors to stderr
- No hardcoded user paths (use `$HOME` or relative)
- Named with a `.sh` extension in source

## Deployment

`install.sh` deploys every `.config/scripts/*.sh` to `/usr/local/bin/`
with the `.sh` extension stripped:

| Source | Deployed as | Invoke with |
|---|---|---|
| `.config/scripts/check-firmware.sh` | `/usr/local/bin/check-firmware` | `check-firmware` |
| `.config/scripts/help.sh` | `/usr/local/bin/help` | `help` |
| `.config/scripts/unseal.sh` | `/usr/local/bin/unseal` | `unseal` |

No bash aliases are needed — `/usr/local/bin/` is in the default `$PATH`.

## Adding a new utility

1. Create `<name>.sh` in `.config/scripts/`
2. `install.sh` picks it up automatically via the glob loop
3. Use it as `name` from anywhere (no alias, no full path)
4. If it needs a sway keybinding, reference `/usr/local/bin/<name>`

## Waybar-internal scripts

Scripts in `.config/waybar/scripts/` are **not** user-facing — they are
called by waybar config or sway keybindings with full paths. They do not
follow the global deployment pattern.
