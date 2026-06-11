# allowlist — Browser Policy Control

Controls URL whitelist filtering across Brave and Firefox via enterprise policies.

## Commands

Run from a root shell (`su -`). Before sudo removal, prefix with `sudo`.

| Command | What it does |
|---|---|
| `/opt/allowlist/allowlist.sh lock` | Enable URL whitelist — only domains in `allowlist.txt` are reachable |
| `/opt/allowlist/allowlist.sh unlock` | Disable URL whitelist — all sites allowed (debloat + DoH remain active) |
| `/opt/allowlist/allowlist.sh toggle` | Switch between locked and unrestricted |
| `/opt/allowlist/allowlist.sh status` | Show current mode and domain count |
| `/opt/allowlist/allowlist.sh add <dom>` | Add domain to allowlist (auto-redeploys if locked) |
| `/opt/allowlist/allowlist.sh remove <dom>` | Remove domain from allowlist (auto-redeploys if locked) |
| `/opt/allowlist/allowlist.sh search <pat>` | Find domains matching pattern |
| `/opt/allowlist/allowlist.sh list` | List all allowed domains |
| `/opt/allowlist/allowlist.sh verify` | Run full system verification |
| `/opt/allowlist/allowlist.sh seal` | Seal recovery credentials with timelock encryption |

## First-run Flow

Before sudo removal, prefix commands with `sudo`. After `su -`, run directly.

```bash
sudo ./install.sh                                # installs everything, no policies deployed
# (as mike + sudo)
sudo /opt/allowlist/allowlist.sh list            # review default domains
sudo /opt/allowlist/allowlist.sh add example.com # add domains for your focus session
sudo gpasswd -d mike sudo                        # remove sudo
# (as root via su -)
su -
/opt/allowlist/allowlist.sh lock                 # activate URL whitelist
# ... focus time ...
/opt/allowlist/allowlist.sh unlock               # edit domains for next session
/opt/allowlist/allowlist.sh remove old.com
/opt/allowlist/allowlist.sh add new.com
/opt/allowlist/allowlist.sh lock                 # lock again
```

## Verify

| Browser | URL to check |
|---|---|
| Brave | `chrome://policy` |
| Firefox | `about:policies` |

## State File

Current mode is stored at `/opt/allowlist/mode` (root-owned). After `install.sh`, it's `unrestricted`.

## Allowlist File

Domains are stored in `/opt/allowlist/allowlist.txt` (root-owned, one per line). Use `/opt/allowlist/allowlist.sh add`/`remove` to edit (from a root shell).

## Verify System State

Run a comprehensive check of all layers against the current mode:

```bash
/opt/allowlist/verify.sh
```

Or via the allowlist command:

```bash
/opt/allowlist/allowlist.sh verify
```

Tests: mode consistency, nftables rules, NextDNS daemon, system DNS, DNS resolution (user), DNS resolution (sudo), container DNS, `tle` binary, PolicyKit rule deployment.
