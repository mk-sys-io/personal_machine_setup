# Why not Ansible or a dotfile manager

---

## Why not Ansible

Ansible is a **fleet orchestration tool** — designed to run the same playbook across hundreds of servers. This repo manages one workstation.

| Cost | Detail |
|------|--------|
| **New dependency** | Requires Python + `ansible-core` + collections installed on the target |
| **YAML indirection** | Core operations are `cp`, `apt install`, and `sed`. Ansible wraps these in YAML modules. When they fail, you debug two layers (Ansible error → wrapped bash error) instead of one |
| **Lockdown has no modules** | `seal_lib.py`, `enter-internet-netns`, `generate-policies.sh`, `nftables reloads` — none of these have Ansible modules. Every one becomes a `command:` or `shell:` module, which is just bash in YAML |
| **No abstraction benefit** | Your package installs span apt, GitHub releases, go install, cargo build, and curl-piped-to-bash. Ansible has no unified interface for these — you still write method-specific shell commands |
| **Make is already present** | Every Linux machine has `make`. Zero dependencies, directly expresses file-level dependencies, and trivially chains shell commands with error handling |

---

## Why not a dotfile manager (chezmoi, Stow, yadm)

Dotfile managers solve **multi-machine divergence** — different paths, packages, or secret keys per host. You have one machine. Their selling points don't apply.

### Chezmoi

| Feature | What it does | Why it doesn't fit |
|---------|-------------|-------------------|
| Go templates | Machine-specific file content | One machine, one config — your `@VARIABLE@` theme system is simpler |
| `.chezmoi` removal protection | Files survive repo deletion | Lockdown files are root-owned — if you delete them, you want them gone |
| Age-encrypted secrets | Secret per-host values | You already have `seal` for credentials |
| `run_once`/`run_onchange` scripts | Conditional execution | `Makefile` targets do this with zero new syntax |
| **Lockdown gap** | No support for root-owned `/etc/`/`/opt/` deployment | The lockdown layer stays as custom `make lockdown` either way, making chezmoi an additional tool for half the repo |

### GNU Stow

| Concern | Why it doesn't fit |
|---------|-------------------|
| Symlinks | Removing a file from the repo removes it from `~/.config/`. For root-owned `/etc/` files this is a security anti-pattern |
| No templates | Stow has no variable substitution — your theme system would need a separate preprocessing step anyway |
| **Lockdown gap** | Same as chezmoi — can't handle `/opt/allowlist` or `/etc/sudoers.d/` |

### yadm

| Concern | Why it doesn't fit |
|---------|-------------------|
| Git wrapper | You already use git directly — yadm adds no new capability |
| Alternate files | For per-machine differences — you have one machine |
| Bootstrap | A `Makefile` is a more explicit and debuggable bootstrap mechanism than yadm's inline bootstrap |

---

## What you already have, and why it's sufficient

| Need | Current tool | Why it's enough |
|------|-------------|-----------------|
| Deploy configs | `Makefile` + `cp` | Explicit, idempotent, debuggable. Symlinks add risk for no gain |
| Declare packages | Makefile method lists (`APT_PACKAGES`, `GO_INSTALLS`, etc.) | One line per tool, one recipe per method. Zero new syntax |
| Theme variables | `@VARIABLE@` + `grep`/`sed` or `envsubst` | Far simpler than Go templates, no new tooling |
| Error handling | `$(call step, ...)` wrapper | Logged, resumable, no YAML abstraction leak |
| Abstraction layer | `lockdown/lib/` adapter scripts | The only bespoke part of the system — no generic tool can help here |

The lockdown system is inherently custom. No existing dotfile manager or automation tool has modules for seal/unseal, nftables generation, or internet network namespace management. These will always be custom shell scripts or Python — and `make` orchestrates them with less friction than any alternative.
