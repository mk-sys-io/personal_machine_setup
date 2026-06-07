**Minimalist Secure Browser Configuration** 

**DEBIAN NETINSTALL + SWAY WAYLAND ENVIRONMENT** 

## **1. Objective & Architectural Goal** 

The objective is to deploy a high-performance, privacy-preserving, and visually clean web browser instance inside a lightweight terminal-based system (Debian Netinstall running a Sway Wayland compositor). To bypass the resource overhead of traditional desktop environments, we leverage Chromium running natively on Wayland. 

Network enforcement is safely handled at the hardware or DNS topology layer via NextDNS, meaning the local web application container can focus entirely on local execution, cookie persistence, extensions lifecycle management, and window structure optimization. 

## **2. Installation Phase** 

First,  we  fetch  the  foundational  packages.  Chromium  is  selected  because  it  completely  isolates  the application structure when invoked via specific window managers. Execute the following in your terminal to synchronize repositories and install Chromium: 

```
sudo apt update
sudo apt install chromium -y
```

## **3. Managed Enterprise Extensions Policy** 

To ensure privacy extensions are strictly enforced, injected on startup, and impossible to accidentally remove, we implement a Linux Enterprise System Policy. This structure loads extensions via hardcoded unique identifiers directly from the Chrome Web Store. 

Create the target policy framework layout using root privileges: 

```
sudo mkdir -p /etc/chromium/policies/managed
```

Generate a configuration policy file named `/etc/chromium/policies/managed/kiosk_policy.json` :

```json
{
  "IncognitoModeAvailability": 1,
  "BrowserGuestModeEnabled": false,
  "PasswordManagerEnabled": false,
  "ExtensionInstallForcelist": [
    "cjpalhdlnbpafiamejdnhcphjbkeiagm;https://clients2.google.com/service/update2/crx",
    "nngcegbndaddmdaobaadofmlidjmjhna;https://clients2.google.com/service/update2/crx",
    "pkehgijbbdfpndfillndmdaidbpeboom;https://clients2.google.com/service/update2/crx"
  ],
  "ExtensionSettings": {
    "*": {
      "installation_mode": "blocked",
      "blocked_install_message": "Only administrator-approved extensions are permitted."
    }
  }
}
```

## **Extension IDs Enforced Above:**

- `cjpalhdlnbpafiamejdnhcphjbkeiagm` — uBlock Origin (Content Blocking)
- `nngcegbndaddmdaobaadofmlidjmjhna` — Bitwarden (Password Management Vault)
- `pkehgijbbdfpndfillndmdaidbpeboom` — Privacy Badger (Heuristic Tracker Blocker) 

## **4. Creating the Persistent Launch Engine** 

To preserve authentication parameters (such as logging into your Bitwarden Vault or keeping stateful cookies for specific sites) without contaminating a generalized profile, we isolate the execution layer to a custom, fixed directory. 

Write the deployment script to `~/.local/bin/launch-secure-browser` : 

```
#!/usr/bin/env bash
export OZONE_PLATFORM=wayland
```

```
# Dedicated stateful directory for minimal browser storage
PROFILE_DIR="$HOME/.config/chromium-minimal"
```

```
chromium   --user-data-dir="$PROFILE_DIR"   --no-first-run   --no-default-browser-
check   --password-store=basic   --app=https://example.com
```

Ensure the script is flagged with executable runtime permissions: 

```
chmod +x ~/.local/bin/launch-secure-browser
```

## **5. Sway Keybinding Setup** 

To tie the initialization sequence to the Sway environment seamlessly, configure the environment shortcut engine. Open your Sway configuration layout (typically located at `~/.config/sway/config` ) and insert the following instruction: 

```
# Launch clean minimal browser instance
bindsym $mod+g exec ~/.local/bin/launch-secure-browser
```

Reload the Sway compositor interface at runtime by issuing the `$mod+Shift+c` directive. Pressing **$mod + g** will now safely spin up the ultra-minimal, privacy-hardened application frame instantly. 

2 

