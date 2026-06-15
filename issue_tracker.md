# Issue Tracker

## [1] GitHub API rate limit blocks localsend/obsidian install

**Status**: Open

**Description**: `install.sh` fetches the latest `.deb` URL from the GitHub API for localsend and obsidian. The unauthenticated GitHub API is limited to 60 requests/hour. By the time install.sh reaches these blocks, earlier curl calls (Brave key, Chrome key, ydotool, opencode, Zed, tle) often exhaust the budget, causing the API to return 403.

**Impact**:
- Localsend: skipped with `WARNING: Could not determine latest LocalSend URL, skipping`
- Obsidian: skipped with `WARNING: Could not determine latest Obsidian URL, skipping`
- Non-critical — both are optional tools; script continues normally

**Root cause**:
- `set -euo pipefail` + raw GitHub API call without auth token
- 60 req/h unauthenticated rate limit consumed by earlier script steps

**Fix**:
- Add optional `GITHUB_TOKEN` support to authenticate API calls (5000 req/h):
  ```
  GITHUB_AUTH=""
  if [ -n "${GITHUB_TOKEN:-}" ]; then
      GITHUB_AUTH="-H Authorization: token $GITHUB_TOKEN"
  fi
  ```
- The `|| true` guard (already added) prevents `set -e` abort when the call fails

**Workaround**: Set `GITHUB_TOKEN` in environment before running install.sh, or simply re-run install.sh later when the rate limit resets.

---

## [2] CopyQ alternatives discussion

**Status**: Open

**Description**: CopyQ is the current clipboard manager, but it has
drawbacks — Qt dependency, complex UI for a simple clipboard, and
occasional paste delays on Wayland. Evaluate lighter alternatives
(cliphist, wl-clipboard + custom script, etc.) that integrate better
with a minimal Sway environment.

---

## [3] NumLock on by default at boot

**Status**: Open

**Description**: NumLock is off after boot on a fresh Debian + Sway
install. Hardware numlock key press works but is manual every time. No
consistent mechanism across Sway/Wayland to set it on login. Need a
udev rule or early boot script.

---

## [4] Refactor system documentation

**Status**: Open

**Description**: Documentation is scattered across multiple files
(`docs/white_internet_policy.md`, `docs/phase4.md`, `manual_work.md`,
`observations.md`, `issue_tracker.md`) with overlapping content and no
clear separation of audience (user vs developer vs architecture).
Propose consolidating into a standard structure:

- `README.md` — quickstart / what is this
- `docs/decisions.md` — ADR-style architecture decision log
- `docs/` — reference (phase4, allowlist, root ownership inventory, etc.)
- `CHANGELOG.md` — history of changes
- Retire stale files (`observations.md`, `plan.md`)

---

## [5] iwlwifi firmware/kernel version drift monitoring

**Status**: Open

**Description**: The Intel AX201 crashes into an unrecoverable hardware
state when `firmware-iwlwifi` and `linux-image` package versions drift
apart. Currently there is no automated check to detect when an apt
upgrade would install an incompatible pair.

**Goal**: A standalone monitoring script (outside `/opt/allowlist/`)
that:
- Parses the loaded iwlwifi firmware version from dmesg
- Compares it against available firmware files shipped by the package
- Warns if the active firmware source would change after an upgrade

Not currently implemented — filed for future reference.

---

## [6] Time-based domain blocking: DNS-level vs browser extension

**Status**: Open

**Description**: Feature request to allow domains in the allowlist to be
blocked/unblocked on a schedule (e.g. block social media during work hours).

**DNS-level approach** (not recommended):
- Annotate `allowlist.txt` entries with time labels, e.g. `*.youtube.com workhours`
- Systemd timer to regenerate dnsmasq config + reload at schedule boundaries
- Pro: system-wide enforcement across all apps/browsers
- Con: significant new code, root-only, limited to whole-domain granularity

**Browser extension approach** (recommended):
- Use Lee​chBlock or similar for per-domain time scheduling
- Pro: minutes to set up, path-level granularity, per-browser profiles, no root
- Con: bypassable via another browser; doesn't affect CLI tools

**Decision**: Browser extensions are sufficient for a single-machine setup.
Filed for reference if system-wide enforcement is ever needed.
