# allowlist — Browser Policy Control

Controls URL whitelist filtering across Brave and Firefox via enterprise policies.

## Commands

| Command | What it does |
|---|---|
| `allowlist lock` | Enable URL whitelist — only domains in `allowlist.txt` are reachable |
| `allowlist unlock` | Disable URL whitelist — all sites allowed (debloat + DoH remain active) |
| `allowlist toggle` | Switch between locked and unrestricted |
| `allowlist status` | Show current mode and domain count |
| `allowlist add <dom>` | Add domain to allowlist (auto-redeploys if locked) |
| `allowlist remove <dom>` | Remove domain from allowlist (auto-redeploys if locked) |
| `allowlist search <pat>` | Find domains matching pattern |
| `allowlist list` | List all allowed domains |

## First-run Flow

```bash
sudo ./install.sh                     # installs everything, deploys unrestricted policy
# (set up NextDNS manually: sign up → sudo nextdns install <id>)
allowlist status                      # verify: Mode: unrestricted
allowlist lock                        # activate URL whitelist
allowlist add duckduckgo.com          # add a domain, auto-redeploys
allowlist unlock                      # back to unrestricted browsing
```

## Verify

| Browser | URL to check |
|---|---|
| Brave | `chrome://policy` |
| Firefox | `about:policies` |

## State File

Current mode is stored at `~/.config/allowlist-mode`. After `install.sh`, it's `unrestricted`.

## Allowlist File

Domains are stored in `~/linux_setup/.config/allowlist.txt` (one per line). Edit manually or use `allowlist add`/`remove`.

## Verify System State

Run a comprehensive check of all layers against the current mode:

```bash
~/.config/waybar/scripts/verify.sh
```

You can also run verification via the allowlist alias:

```bash
allowlist verify
```

Tests: mode consistency, nftables rules, NextDNS daemon, system DNS, DNS resolution (user), DNS resolution (sudo), container DNS, `tle` binary, PolicyKit rule deployment.
