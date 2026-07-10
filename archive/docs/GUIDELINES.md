# Documentation Guidelines

This file defines the purpose, scope, and audience for every document
in `docs/`. Read this first before adding content to any file — it
ensures new content goes in the right place.

If a file does not exist yet, create it according to its guideline here.
If a file has no guideline, add one.

---

## allowlist.md

- **Purpose:** CLI command reference for the `allowlist` utility
- **Scope:** Every subcommand, flag, and workflow (lock, unlock, toggle, status, search, list, clear-session, verify, seal)
- **Audience:** Operator
- **Does NOT belong:** Design rationale, architecture, implementation details
- **Related:** operator_guide.md (step-by-step workflows), architecture.md (why it exists)

## architecture.md

- **Purpose:** System design and four-phase lockdown architecture
- **Scope:** High-level design, phase goals, component interaction, design decisions
- **Audience:** Architect, developer
- **Does NOT belong:** Day-to-day commands, step-by-step procedures
- **Note:** This file does not exist yet — it will consolidate white_internet_policy.md

## container_usage.md

- **Purpose:** Podman container cheatsheet
- **Scope:** Common commands, Alpine-first strategy rationale, temp vs persistent patterns
- **Audience:** Operator
- **Does NOT belong:** Browser setup, allowlist management, seal/recovery

## decisions.md

- **Purpose:** Architecture Decision Record (ADR) log
- **Scope:** Formal numbered decisions with Date, Context, Decision, Consequences
- **Audience:** Architect, developer
- **Rule:** One ADR per significant architectural choice. Append-only — never edit past entries.

## desktop_config.md

- **Purpose:** Desktop environment configuration reference
- **Scope:** Sway, Waybar, Foot, Fuzzel, CopyQ, Catppuccin Mocha theme, keybinding philosophy
- **Audience:** Operator, developer
- **Note:** This file does not exist yet — it will consolidate content from phase2.md and plan.md

## issue_tracker.md

- **Purpose:** Open, closed, and deferred issues and feature requests
- **Location:** Root level (`issue_tracker.md`)
- **Scope:** One issue per section with Status label (Open/Closed/Deferred)
- **Audience:** Developer, operator
- **Does NOT belong:** Design decisions (→ decisions.md), completed work (→ changelog.md)

## manual_work.md

- **Purpose:** One-time manual steps after running install.sh
- **Scope:** Only what requires human judgment after the installer finishes
- **Audience:** Operator
- **Rule:** If a step can be automated, fix install.sh instead. No recurring issues. No reference material. No design rationale.

## network_architecture.md

- **Purpose:** Two-mode DNS and firewall traffic flows
- **Scope:** Component-role table, traffic flow diagrams, verify test behavior
- **Audience:** Architect, operator
- **Does NOT belong:** CLI commands, step-by-step procedures

## nvidia_attempt.md

- **Purpose:** Post-mortem of failed NVIDIA proprietary driver migration
- **Scope:** What was changed, what happened, root cause, conclusion, revert mechanism
- **Audience:** Developer, operator
- **Rule:** Preserve as-is — historical record. Add status header if revisited.

## operator_guide.md

- **Purpose:** Day-to-day system operation
- **Scope:** Lock/unlock, verify, add domains, seal, recover, WiFi troubleshooting
- **Audience:** Operator
- **Note:** This file does not exist yet — it will consolidate content from manual_work.md

## phase2.md

- **Purpose:** Phase 2 desktop environment design
- **Scope:** Catppuccin palette, Waybar geometry, Sway borders, CopyQ decision
- **Audience:** Architect, developer
- **Status:** Content will be absorbed into desktop_config.md, then retired

## phase4.md

- **Purpose:** Phase 4 seal and sudo removal design
- **Scope:** Seal command flow, recovery path, post-sudo capability table
- **Audience:** Architect, operator
- **Status:** Content to be consolidated into seal.md and architecture.md

## plan.md

- **Purpose:** Original project plan and phase overview
- **Scope:** Phase 1 components, tool migration history, decision log, future ideas
- **Audience:** Architect, developer
- **Status:** Reference only — no longer updated. Future ideas moved to issue_tracker.md.

## root_ownership_inventory.md

- **Purpose:** Security inventory of all root-owned files
- **Scope:** Per-file ownership, permissions, immutable flag, risk analysis per layer
- **Audience:** Architect, developer
- **Does NOT belong:** General usage instructions, CLI command reference

## seal.md

- **Purpose:** Seal/unseal subsystem mechanics
- **Scope:** Directory layout, seal cycle (init → encrypt → shred → lock → reboot), unseal flow, security model, file reference table
- **Audience:** Operator, developer
- **Does NOT belong:** Post-install setup steps (→ operator_guide.md)

## troubleshooting.md

- **Purpose:** Recurring failure modes with no software fix
- **Scope:** Only issues requiring manual intervention every time
- **Audience:** Operator
- **Rule:** If root cause can be patched in install.sh or a script, the fix goes there — not here

## user_reference.md

- **Purpose:** Inventory of every user-facing CLI tool, alias, and command
- **Scope:** Tables of utilities, allowlist commands, bash aliases, service management
- **Audience:** Operator
- **Does NOT belong:** How-to procedures, design rationale, troubleshooting steps

## utility_scripts.md

- **Purpose:** Convention and deployment pattern for user-facing CLI utilities
- **Scope:** Script standards, deployment from .config/scripts/ to /usr/local/bin/, two-tier distinction (user-facing vs Waybar-internal)
- **Audience:** Developer

## white_internet_policy.md

- **Purpose:** Comprehensive architecture and design for the White Internet Policy system
- **Scope:** All four phases, ASCII architecture diagrams, file references, design decisions
- **Audience:** Architect, developer
- **Status:** Will be split into architecture.md (design) and operator_guide.md (procedures), then retired

## browser_setup.md

- **Purpose:** Minimalist secure browser configuration guide
- **Scope:** Brave/Chrome/Firefox policy deployment, extension force-install, debloating
- **Audience:** Operator, developer
- **Note:** This file does not exist yet — it will be renamed from minimal_browser_setup_guide.md

---

## Domain file conventions

**Location:** `.config/allowlist/domains/`

**Scope:** Three domain files control DNS resolution and bookmark generation:

| File | DNS | Bookmarks | Cleared by |
|---|---|---|---|
| `base.txt` | Always | Yes — every entry | Never |
| `session.txt` | Always | Yes — every entry | `allowlist clear-session` |
| `infra.txt` | Always | No | Never |

**Rules:**
- Every non-comment line in `base.txt` and `session.txt` produces a bookmark. There are no hidden filters.
- `infra.txt` is DNS-only and never generates bookmarks.
- `deny.txt` overrides allowlist entries at the DNS level (locked mode only).
- Comments (`#`) and blank lines are ignored.
- Wildcard entries (`*.example.com`) generate a bookmark for the bare domain (`example.com`).

**Rationale:** Search engines render image/video search results that can leak
soft porn even with SafeSearch. In locked mode, these must be explicitly denied
at the DNS level while allowing productivity subdomains via wildcard entries.
