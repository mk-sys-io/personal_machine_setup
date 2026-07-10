# allowlist — DNS + Browser Policy Control

Controls DNS whitelisting via dnsmasq and deploys browser debloat policies with auto-generated bookmarks.

## Commands

Run from a root shell (`su -`). Before sudo removal, prefix with `sudo`.

| Command | What it does |
|---|---|
| `allowlist lock` | Enable DNS whitelist — only allowlisted domains resolve |
| `allowlist unlock` | Disable DNS whitelist — all domains resolve |
| `allowlist toggle` | Switch between locked and unrestricted |
| `allowlist status` | Show current mode and per-section domain counts |
| `allowlist search <pat>` | Find domains matching pattern across all sections |
| `allowlist list [--section]` | List all domains; `--infra`, `--base`, `--session` to filter |
| `allowlist clear-session` | Remove all session domains and redeploy |
| `allowlist verify` | Run full system verification |
| `allowlist seal` | Seal recovery credentials with timelock encryption |

## Allowlist Files

Domains are stored in three root-owned files at `/opt/allowlist/`:

| File | Purpose | Bookmarks? | Clearable? |
|---|---|---|---|
| `allowlist.infra.txt` | Backend-only domains (CDN, APIs, auth). Needed by tools, never visited in browser. | No | Never |
| `allowlist.base.txt` | Permanent browsing domains. Visited in browser, also critical infrastructure. | Yes | Never |
| `allowlist.session.txt` | Temporary browsing domains. Added per-task or per-session. | Yes | `clear-session` |

All three files are **always concatenated** for DNS resolution. Every domain always resolves.
The `*.` prefix (e.g. `*.github.com`) works with dnsmasq's native subdomain matching.

## Editing the Allowlist

All files are root-owned (`root:root`, mode 640). To edit:

1. **Unlock first:** `allowlist unlock`
2. **Edit the appropriate file** with your preferred editor:
   ```
   sudo <editor> /opt/allowlist/allowlist.session.txt
   ```
3. **Lock to redeploy:** `allowlist lock`

Or from a root shell (`su -`): edit directly, then `allowlist lock`.

**Important:** The allowlist cannot be altered when locked without root access.
After Phase 4 (sealing), the root password is timelock-encrypted. To edit after sealing:
wait for timelock expiry, run `unseal` to recover credentials, `su -`, edit, re-lock, re-seal.

Which section to edit:
- Session (`session.txt`) — quick temporary additions for the current task
- Base (`base.txt`) — a domain you visit regularly that should stay permanently
- Infra (`infra.txt`) — a backend domain your tools need (rarely changed)

## Bookmarks

Bookmarks are auto-generated from `allowlist.base.txt` + `allowlist.session.txt` and
deployed to Brave, Chrome, and Chromium via the `ManagedBookmarks` policy.
Firefox is excluded.

Entries with `*.` (e.g. `*.github.com`) become bookmarks for their apex domain (`https://github.com`).
Concrete subdomains (e.g. `mail.google.com`) become bookmarks for that exact URL.

Bookmarks update whenever you run `allowlist lock` or `allowlist unlock`.

## State File

Current mode is stored at `/opt/allowlist/mode` (root-owned). After `install.sh`, it's `unrestricted`.

## First-run Flow

```bash
sudo ./install.sh                                                  # install everything
sudo /opt/allowlist/allowlist.sh list --base                       # review permanent domains
sudo /opt/allowlist/allowlist.sh list --session                    # review session domains
sudo <editor> /opt/allowlist/allowlist.session.txt                 # add temporary domains
sudo gpasswd -d mike sudo                                          # remove sudo
su -
/opt/allowlist/allowlist.sh lock                                   # activate DNS whitelist
# ... focus time ...
/opt/allowlist/allowlist.sh unlock                                 # edit domains
# edit session.txt with <editor>
/opt/allowlist/allowlist.sh lock                                   # lock again
```

## Verify

| Browser | URL to check |
|---|---|
| Brave/Chrome | `chrome://policy` |
| Firefox | `about:policies` |

Verification covers: mode consistency, nftables rules, dnsmasq daemon, system DNS,
DNS resolution (user + root), container DNS, `unseal` binary, `tle` binary, PolicyKit rules.
