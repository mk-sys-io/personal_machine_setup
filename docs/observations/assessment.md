# Documentation Assessment

Generated: 2026-06-20
Context: Full codebase analysis by opencode agent

---

## What's Working Well

- **ADR quality:** `decisions.md` uses a clean, consistent ADR format (Date, Status, Context, Decision, Consequences) that makes architectural history auditable.
- **Cross-referencing:** Docs reference each other explicitly (`docs/white_internet_policy.md` links to `docs/allowlist.md`, `docs/seal.md`, `docs/decisions.md`, etc.) and reference code paths (`/opt/allowlist/allowlist.sh`, `/etc/polkit-1/rules.d/...`). This creates a usable hypertext.
- **Root ownership inventory:** `root_ownership_inventory.md` is meticulous — per-file risk analysis, per-layer breakdown (DNS → Firewall → Browser Policy → PolicyKit → Crypto), and distinguishes "denial vs bypass" threats.
- **Verify.sh as executable spec:** The verification suite in `verify.sh` directly corresponds to documented expectations — the docs and tests agree.
- **Honest retrospective:** `observations.md` openly acknowledges gaps (no linter, no dry-run, no structured validation) and documents unknown-unknowns with a structured table.
- **Failed-experiment preservation:** `nvidia-attempt.md` is a model of how to document a dead end (what changed, what happened, root cause, conclusion, revert mechanism).
- **Security mindset:** `root_ownership_inventory.md` and `white_internet_policy.md` are written with a clear adversary model (user mike cannot bypass the lockdown).

---

## What Is Unclear, Missing, or Potentially Misleading

- **No entry point.** There is no `README.md` at the root. A new reader (including future-you) has no place to start. `docs/plan.md` is the closest thing but mixes project history with design decisions.
- **Phase model is ossifying.** The system now has feature requests (issue_tracker.md items #6–#14) that don't fit into Phase 1–4. `plan.md` and `phase4.md` imply a "finished" system when the project is actively evolving.
- **Duplication across docs:**
  - `white_internet_policy.md` (606 lines) covers all four phases comprehensively, while `phase4.md` and parts of `plan.md` cover the same ground at different granularities.
  - `manual_work.md` (root level) duplicates seal/recovery instructions found in `docs/seal.md` and `docs/allowlist.md`.
  - Firefox policies are documented in three places (`minimal_browser_setup_guide.md`, `white_internet_policy.md`, `generate-policies.sh` comments).
- **Audience confusion.** It is unclear which documents target an operator (running the system), a developer (modifying it), or an architect (understanding design rationale). `observations.md` mixes all three.
- **Stale/misleading content:**
  - `plan.md` lists tools (kitty, Chromium) that have already been migrated away — the doc describes past tense but still lists them as current.
  - `phase2.md` has a "Decision Log" with a `DEFERRED` entry (auto-move to workspace) that is still unresolved three documents later.
  - `nvidia-attempt.md` exists but the code changes were reverted — no cross-reference explains the current state of NVIDIA config.
- **Missing structural docs:**
  - No document maps the `.config/` directory tree and explains each profile and its relationship to the docs.
  - No document explains `install.sh`'s workflow at a conceptual level (it is self-commenting but has no companion doc).
  - No security-model summary that ties all layers (dnsmasq × nftables × polkit × browser policy × sudoers × root ownership × timelock) into one threat model.
- **`issue_tracker.md` lives at root level**, not in `docs/`, breaking the convention that documentation lives under `docs/`.
- **`observations.md` is a firehose.** It contains important lessons (user-context testing, runtime-vs-write-time validation, Bash vs Ansible analysis) but is hard to navigate — it has no section numbering and uses prose for content that would be better in tables.

---

## Why This System Is Harder to Document Than a Web App

A web app's surface is bounded by its code — routes, controllers, database schema. This system's surface is every Linux subsystem it touches:

- **dnsmasq, nftables, polkit, sudoers, NetworkManager, systemd, podman, drand**
- **Browser enterprise policies** (Brave, Chrome, Firefox all have different policy engines)
- **Wayland, GPU firmware, WiFi firmware, sysfs, cgroups v2, chattr, initramfs**

Each is a complex system with its own semantics, version history, and bug list.

Three things make this fundamentally harder than a web app:

1. **Adversarial surface area.** Every edge case is a potential lockdown bypass. The threat model expands as you think of new angles — "what happens if a kernel update changes nftables behavior?" or "can I boot a USB stick?"

2. **Every escape hatch is a security boundary.** Containers bypass the allowlist by design. The NetworkManager polkit carve-out, the `unseal` wrapper, the sudoers entries — each is a designed exception that must be exactly as wide as needed and no wider.

3. **State space explosion.** A web app has auth state, session state, database state. This system has: locked/unlocked, sealed/unsealed, before-sudo-removal/after, before-reboot/after, container-running/host-daemon, phase-N-of-install, firmware-updated-since-install. Every combination changes the behavior of every component.

The escape hatch churn isn't documentation failure — it's the system maturing. Every time you discover a new bypass, you tighten it. Every time you add a new tool, you create a new boundary to constrain. The underlying subsystems (kernel, firmware, browsers) change without your consent.

---

## Architectural, Organizational, and Maintenance Risks

| Risk | Severity | Detail |
|------|----------|--------|
| Doc-code drift | High | `allowlist.sh` is 425 lines with no test framework beyond `verify.sh`. As new features are added (issues #7–#14), the CLI grows; docs and code will silently diverge. |
| Phase-model friction | Medium | New features (temp domain escape hatch, per-app network access, unified CLI panel) don't fit Phase 1–4. Newcomers will wonder "are we done?" |
| Single point of knowledge | Medium | `white_internet_policy.md` (606 lines) is the definitive reference. If it falls out of date, there is no secondary source of truth. |
| No tests for docs | Low–Medium | No document validates that cross-references resolve (e.g., that `docs/allowlist.md`'s reference to `docs/seal.md` is still accurate). |
| Orphaned config drift | Low | `numlock-debug.sh` is gitignored at root. `nvidia-attempt.md` refers to code that no longer exists. No mechanism detects stale docs. |
| Container docs assume lockdown knowledge | Low | `container_usage.md` is a cheatsheet, but its "why Alpine" rationale depends on understanding the allowlist architecture — which a reader of that doc might not have yet. |

---

## Purpose of Each Major Area Outside `@docs/`

| Area | Purpose |
|------|---------|
| `.config/sway/` | Sway compositor config: window management, keybindings, input (French AZERTY + NumLock), autostart entries |
| `.config/waybar/` | Status bar layout (JSON), styling (CSS, Catppuccin Mocha palette), and 8 Waybar helper scripts (network, volume, brightness, numlock, clipboard, browser launcher, persistent launcher, nmtui toggle) |
| `.config/foot/` | Terminal emulator: JetBrains Mono 11pt, Catppuccin Mocha palette, 10K scrollback, block cursor |
| `.config/fuzzel/` | App launcher: minimal config, JetBrains Mono, Catppuccin Mocha |
| `.config/copyq/` | Clipboard manager: dark theme, 40-item history, no tray, hidden toolbars + custom Catppuccin theme |
| `.config/nftables/` | Firewall templates: `nftables.conf.base` (unrestricted) and `nftables.conf.locked` (drop DNS from UID 1000) |
| `.config/grub.d/` | Nouveau GSP firmware kernel parameter |
| `.config/allowlist/` | Core lockdown system: 4 domain list files + 5 management scripts (CLI, dnsmasq gen, nftables gen, policy gen, verify) |
| `.config/bashrc` | Bash additions: docker→podman alias, `rm -I`, Sway auto-start, PATH |
| `.config/brave/` | Chromium enterprise policy template: disables bloat, force-installs uBlock + Bitwarden |
| `.config/firefox/` | Firefox enterprise policy template: disables telemetry, Pocket, accounts, DoH |
| `.config/scripts/` | User-facing CLI utilities: check-firmware, help, unseal |
| `.config/polkit/` | PolicyKit lockdown: blocks all pkexec for mike except NetworkManager |
| `.config/sudoers/` | Restricted sudo: apt, systemctl status, journalctl, reboot/poweroff/suspend, timeshift |
| `.config/obsidian/` | Obsidian dark theme override |
| `install.sh` | 410-line install/orchestration script: packages, services, configs, DNS, allowlist |
| `keybindings.md` | Sway keybinding reference table (23 entries) |
| `manual_work.md` | Post-install manual procedure: browser check, firmware, root password, seal, recovery, WiFi |
| `issue_tracker.md` | 14 tracked items (8 open, 3 closed, 1 failed) |
| `numlock-debug.sh` | Debug monitor for NumLock state (gitignored) |

---

## Recommended Documentation Strategy

### Structural Principles

1. **Root README.md as the entry point.** One page: "what is this repo, how to install it, where to find docs."
2. **Separate by audience:** operator docs (how to use), developer docs (how to modify), architecture docs (why it is this way).
3. **ADR as the permanent record.** `decisions.md` is already good — keep it and expand it.
4. **Consolidate phase docs.** Retire the phase numbering for new work; use functional categories (Lockdown, Desktop, Tooling) instead.
5. **`docs/` is the canonical docs directory.** Keep everything here.

### Missing Documents to Add

| Document | Purpose |
|----------|---------|
| `docs/README.md` or root `README.md` | Entry point, repo overview, quickstart |
| `docs/file_tree.md` | Annotated map of every file in `.config/` and `/opt/allowlist/` with ownership, purpose, and doc cross-ref |
| `docs/security_model.md` | Threat model tying all layers together |
| `docs/operator_guide.md` | Day-to-day operations: lock/unlock, verify, add domains, recover, WiFi |
| `docs/developer_guide.md` | How to add scripts, modify the allowlist flow, run install.sh idempotently |
| `docs/changelog.md` | Brief chronological record of significant changes |
| `docs/troubleshooting.md` | Diagnosing common failure modes (WiFi crash, dnsmasq down, policy errors, tle unreachable, verify failures) |
| `docs/user_reference.md` | All user-facing CLI tools, aliases, and their purposes |

### Documents to Consolidate or Retire

| Document | Action | Reason |
|----------|--------|--------|
| `docs/plan.md` | Retire → absorb into architecture.md and changelog.md | Outdated phase model |
| `docs/phase2.md` | Retire → absorb into desktop_config.md (new) | Phase-specific, content is stable |
| `docs/phase4.md` | Consolidate into `docs/seal.md` and `docs/white_internet_policy.md` | Duplicates both |
| `docs/nvidia-attempt.md` | Keep with status header: "Reverted — system uses nouveau" | Valuable post-mortem |
| `docs/minimal_browser_setup_guide.md` | Keep, consolidate with policy template docs | Valuable standalone |
| `manual_work.md` (root) | Move to `docs/operator_guide.md` | Duplicates seal.md and allowlist.md |
| `issue_tracker.md` (root) | Move to `docs/issues.md` | Consistency |

---

## Proposed Documentation Hierarchy

```
/
├── README.md                          # NEW — entry point, quickstart, doc tree
├── keybindings.md                     # KEEP (used by help.sh at runtime)
├── install.sh                         # Code — self-documenting with comments
│
└── docs/
    ├── README.md                      # NEW — navigation index for docs/
    ├── architecture.md                # CONSOLIDATED from white_internet_policy.md
    │                                   # (strip operator details, keep design)
    ├── operator_guide.md              # NEW — extracted from manual_work.md
    │   ├── Installation
    │   ├── Lock/unlock workflow
    │   ├── Adding/removing domains
    │   ├── Verification
    │   ├── Seal & recovery
    │   └── WiFi troubleshooting
    ├── decisions.md                   # KEEP — ADR log
    ├── security_model.md              # NEW — threat model
    ├── file_tree.md                   # NEW — annotated config map
    ├── troubleshooting.md             # NEW — common failures + fixes
    ├── user_reference.md              # NEW — utilities, aliases, commands
    ├── desktop_config.md              # NEW — consolidated from phase2.md + plan.md
    ├── browser_setup.md               # RETAINED from minimal_browser_setup_guide.md
    ├── container_usage.md             # KEEP
    ├── seal.md                        # KEEP (consolidated from phase4.md)
    ├── allowlist.md                   # KEEP (CLI reference)
    ├── network_architecture.md        # KEEP
    ├── utility_scripts.md             # KEEP
    ├── nvidia_attempt.md              # KEEP (with status header)
    ├── changelog.md                   # NEW — curated git history
    ├── issues.md                      # MOVED from issue_tracker.md
    │
    └── observations/                  # GITIGNORED — scratch workspace
        ├── observations.md            # MOVED from docs/observations.md
        ├── assessment.md              # THIS FILE
        └── future_references.md       # Drafts for troubleshooting/user_reference
---

## Prioritized Action Plan

| Priority | Action | Effort | Impact |
|----------|--------|--------|--------|
| **P0** | Write root `README.md` | 1h | High — removes onboarding barrier |
| **P0** | Write `docs/README.md` with doc index | 30m | High — makes doc tree navigable |
| **P1** | Consolidate `white_internet_policy.md` → `architecture.md` | 3h | Medium — reduces duplication |
| **P1** | Extract `docs/operator_guide.md` from `manual_work.md` + `allowlist.md` + `seal.md` | 2h | High — separates audience |
| **P1** | Write `docs/security_model.md` | 2h | High — fills critical gap |
| **P1** | Write `docs/troubleshooting.md` | 2h | High — most-needed practical ref |
| **P1** | Write `docs/user_reference.md` | 1h | High — what commands/aliases exist |
| **P2** | Move `issue_tracker.md` → `docs/issues.md` | 10m | Low — consistency fix |
| **P2** | Write `docs/file_tree.md` | 1h | Medium — prevents file-discovery friction |
| **P2** | Restructure `observations.md` into sections | 1h | Medium — improves navigation |
| **P2** | Retire `docs/plan.md` and `docs/phase2.md` | 30m | Low — removes stale content |
| **P3** | Write `docs/changelog.md` | 2h | Low — nice to have |
| **P3** | Add doc-ref inline comments to all `.config/` files | 1h | Low — prevents future drift |
| **P3** | Write `docs/developer_guide.md` | 2h | Medium — lowers contribution barrier |
