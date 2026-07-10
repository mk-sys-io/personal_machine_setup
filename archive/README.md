# linux_setup — Documentation Index

This README is the entry point for the entire repository.
For doc rules and scope definitions, see `docs/GUIDELINES.md`.

## Before running install.sh

Create a classic personal access token at https://github.com/settings/tokens
with the minimal scopes `repo`, `read:org`, and `gist` selected and **No expiration**.

The install script authenticates the gh CLI with the token, configures
git identity, and passes the token to GitHub API calls during tool
downloads (raising the rate limit from 60 to 5000 requests per hour).

**Either:**
- Fill `.config/github.env` (copy from `.config/github.env.template`), or
- Run `./install.sh` — it prompts for any missing values automatically.

All values are saved to `.config/github.env` after prompting.
Edit that file before re-running to change them.

## Navigation

| File | Purpose |
|------|---------|
| README.md | This file — repo entry point |
| docs/GUIDELINES.md | Scope and audience rules for every doc |
| docs/operator_guide.md | Day-to-day system operation (lock, unlock, verify, seal, recover) |
| docs/user_reference.md | CLI utilities, aliases, and allowlist commands |
| docs/troubleshooting.md | Recurring hardware failures with no software fix |
| docs/allowlist.md | allowlist CLI command reference |
| docs/seal.md | Seal/unseal mechanics and security model |
| docs/container_usage.md | Podman container cheatsheet |
| docs/network_architecture.md | DNS and firewall traffic flows |
| docs/root_ownership_inventory.md | Security inventory of root-owned files |
| docs/utility_scripts.md | Script convention and deployment |
| docs/decisions.md | Architecture Decision Record (ADR) log |
| docs/architecture.md | System design and lockdown architecture |
| docs/security_model.md | Threat model and layer composition |
| docs/desktop_config.md | Sway, Waybar, Foot, Fuzzel, CopyQ config |
| docs/browser_setup.md | Brave/Chrome/Firefox debloating and policy |
| docs/nvidia_attempt.md | Failed NVIDIA driver migration post-mortem |
| docs/changelog.md | Significant changes over time |
| docs/issues.md | Open and closed issue tracker |

## Other files

| File | Purpose |
|------|---------|
| keybindings.md | Sway keybinding reference table |
| manual_work.md | One-time setup steps after install.sh |
| issue_tracker.md | Issue tracker (to be moved to docs/issues.md) |
| docs/observations/ | Private scratch workspace (gitignored) |

## Planned but not yet created

- docs/operator_guide.md
- docs/user_reference.md
- docs/troubleshooting.md
- docs/architecture.md (from white_internet_policy.md)
- docs/security_model.md
- docs/desktop_config.md (from phase2.md + plan.md)
- docs/browser_setup.md (from minimal_browser_setup_guide.md)
- docs/changelog.md
- docs/issues.md (from issue_tracker.md)
