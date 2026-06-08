**Minimalist Secure Browser Configuration**
**DEBIAN NETINSTALL + SWAY WAYLAND ENVIRONMENT**

## **1. Objective & Architectural Goal**

The objective is to deploy a high-performance, privacy-preserving, and visually clean web browser instance inside a lightweight terminal-based system (Debian Netinstall running a Sway Wayland compositor). We use Brave Browser for its native Wayland support, built-in ad/tracker blocking (Brave Shields), and extensive enterprise policies that allow stripping all non-essential features.

## **2. Installation Phase**

Fetch the Brave apt repository and install:

```
curl -fsSL https://brave-browser-apt-release.s3.brave.com/brave-core.asc \
  | sudo gpg --dearmor -o /usr/share/keyrings/brave-browser-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/brave-browser-archive-keyring.gpg] \
  https://brave-browser-apt-release.s3.brave.com/ stable main" \
  | sudo tee /etc/apt/sources.list.d/brave-browser-release.list
sudo apt update
sudo apt install brave-browser -y
```

## **3. Managed Enterprise Debloat Policy**

To strip all non-essential Brave features (Rewards, Wallet, VPN, Leo AI, Tor, News, Talk, telemetry) and enforce only admin-approved extensions, create:

```
sudo mkdir -p /etc/brave/policies/managed
```

Create `/etc/brave/policies/managed/kiosk_policy.json`:

```json
{
  "IncognitoModeAvailability": 1,
  "BrowserGuestModeEnabled": false,
  "PasswordManagerEnabled": false,
  "BraveRewardsDisabled": true,
  "BraveWalletDisabled": true,
  "BraveVPNDisabled": true,
  "BraveAIChatEnabled": false,
  "TorDisabled": true,
  "BraveNewsDisabled": true,
  "BraveTalkDisabled": true,
  "BraveSpeedreaderEnabled": false,
  "BraveWaybackMachineEnabled": false,
  "BraveP3AEnabled": false,
  "BraveStatsPingEnabled": false,
  "BraveWebDiscoveryEnabled": false,
  "BravePlaylistEnabled": false,
  "ExtensionInstallForcelist": [
    "cjpalhdlnbpafiamejdnhcphjbkeiagm;https://clients2.google.com/service/update2/crx",
    "nngceckbapebfimnlniiiahkandclblb;https://clients2.google.com/service/update2/crx"
  ],
  "ExtensionSettings": {
    "*": {
      "installation_mode": "blocked",
      "blocked_install_message": "Only administrator-approved extensions are permitted."
    }
  }
}
```

## **Extensions Enforced:**

- `cjpalhdlnbpafiamejdnhcphjbkeiagm` — uBlock Origin (Content Blocking)
- `nngceckbapebfimnlniiiahkandclblb` — Bitwarden (Password Management Vault)

Brave Shields replaces Privacy Badger for tracker blocking.

## **4. Creating the Launch Engine**

The deployment script is at `~/.config/waybar/scripts/launch-browser.sh` (installed automatically by `install.sh`):

```bash
#!/bin/bash
brave-browser --no-first-run --no-default-browser-check --password-store=basic --force-dark-mode
```

## **5. Sway Keybinding Setup**

The Sway config (`~/.config/sway/config`) already includes this binding (set by `install.sh`):

```
# Launch minimal browser (Brave, Wayland-native)
bindsym $mod+Shift+g exec ~/.config/waybar/scripts/launch-browser.sh
```

Reload Sway with `$mod+Shift+c`. Pressing `$mod+Shift+g` launches the debloated Brave instance.

## **6. Brave-Specific Policy Reference**

| Policy | Effect |
|--------|--------|
| `IncognitoModeAvailability: 1` | Disables private/incognito windows |
| `BrowserGuestModeEnabled: false` | Disables guest mode |
| `PasswordManagerEnabled: false` | Disables built-in password manager |
| `BraveRewardsDisabled: true` | Hides BAT rewards/ads |
| `BraveWalletDisabled: true` | Removes crypto wallet + Web3 |
| `BraveVPNDisabled: true` | Removes VPN button/subscription |
| `BraveAIChatEnabled: false` | Disables Leo AI assistant |
| `TorDisabled: true` | Removes private window with Tor |
| `BraveNewsDisabled: true` | Removes news feed on new tab |
| `BraveTalkDisabled: true` | Removes Brave Talk widget |
| `BraveSpeedreaderEnabled: false` | Disables speedreader mode |
| `BraveWaybackMachineEnabled: false` | Disables 404 Wayback integration |
| `BraveP3AEnabled: false` | Disables telemetry |
| `BraveStatsPingEnabled: false` | Disables usage heartbeat ping |
| `BraveWebDiscoveryEnabled: false` | Disables Web Discovery Project |
| `BravePlaylistEnabled: false` | Disables offline playlist |
