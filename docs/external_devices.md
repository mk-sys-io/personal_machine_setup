# External Device Integration

## Overview

The desktop lockdown is only half the picture. If the phone still has unrestricted access to browsers, social media, YouTube, and app stores, it becomes the path of least resistance the moment the desktop is sealed. A credible behavioral infrastructure must cover every screen.

This document covers Android integration. The goal is not to eliminate the phone but to make it a focused tool — calls, messages, maps, camera, essentials — rather than a pocket-sized internet escape hatch.

---

## Android Setup

### Prerequisites

- Android device — enable Developer Options and USB debugging:
  1. Open **Settings → About phone**
  2. Tap **Build number** 7 times until "You are now a developer!" appears
  3. Go to **Settings → System → Developer options**
  4. Toggle **USB debugging** on
  5. Connect via USB and accept the authorization prompt on the device
- `adb` and `fastboot` from `android-sdk-platform-tools` on the desktop
- Google account with Family Link setup ability
- USB cable

### 1. UAD-NG Debloat — Remove Distractions at Package Level

Debloating uninstalls packages for the current user (not root, no device unlock needed). These survive reboots and factory resets only undo them.

**Safety**: This only removes packages for user `0` (the primary profile). The packages remain installed in the system partition — a factory reset restores everything. Nothing is brickable.

**Setup**: Install the tool and its dependencies via `install.sh`, which builds `uad` from source and installs `android-sdk-platform-tools` (ADB). See `docs/decisions.md` `[006]` for the tool choice rationale.

**Essential commands**:

| Operation | Command |
|-----------|---------|
| Verify device connection | `uad devices` |
| List all system packages | `uad list` |
| Search by name | `uad list -q "facebook"` |
| Filter by safety level | `uad list --removal recommended` |
| Filter by vendor | `uad list --list google` |
| Inspect a package | `uad info <package.name>` |
| Dry-run uninstall | `uad rm <package.name> --dry-run` |
| Uninstall for current user | `uad rm <package.name>` |
| Restore a removed package | `uad enable <package.name>` |
| Refresh package definitions | `uad update` |

**Note**: `uad` starts the ADB server automatically on first use. Run `uad devices` first to verify the device is detected and authorized.

**Package classification**: Every package in the UAD-NG list is tagged with one of four removal levels, fetched automatically from the community-maintained `uad_lists.json`:

| Level | Meaning |
|-------|---------|
| `recommended` | Safe to remove; available via app stores |
| `advanced` | Breaks minor features or removable defaults (keyboard, launcher) |
| `expert` | Breaks important functionality; should not boot-loop |
| `unsafe` | Can break vital OS parts or cause boot loops |

Filter by level to see what can be safely removed on your specific device:

```bash
# discover candidates
uad list --removal recommended > candidates.txt

# inspect before acting
uad info com.android.chrome

# remove with confirmation
uad rm com.android.chrome --dry-run
uad rm com.android.chrome
```

**Debloat script pattern** — grouped by category for clarity:

```bash
# browsers
uad rm com.android.chrome || true
uad rm com.brave.browser || true

# social media
uad rm com.instagram.android || true
uad rm com.facebook.katana || true

# video
uad rm com.google.android.youtube || true

# app store (after installing all needed apps)
uad rm com.android.vending || true
```

After debloating, lock USB debugging off in Developer Options so re-enabling requires physical access to the device.

#### Manual ADB Fallback

If UAD-NG is not available, the raw ADB equivalents are:

```bash
adb start-server                                    # start ADB daemon (if not running)
adb devices                                         # verify device is detected/authorized
adb shell pm list packages                          # list all installed
adb shell pm uninstall -k --user 0 <package.name>   # remove for current user
adb shell pm list packages | sort > installed-packages.txt  # snapshot
```

### 2. Google Family Link — Lock Down Remaining Apps

ADB removes the escape hatches (browser, app store). Family Link provides the ongoing discipline:

- **App time limits** — set daily limits on any remaining non-essential apps
- **Bedtime** — enforced quiet hours (e.g., 22:00-07:00, all apps grayed out)
- **Purchase blocks** — prevent reinstalling removed apps via Play Store
- **App approval** — new app installs require parent account approval

**Setup**: Install Family Link on the device, register as "child" under a throwaway or secondary Google account. This account is only used for device policy — it does not need to be your primary account.

**Without Play Store**: If you debloat `com.android.vending`, Family Link still enforces time limits on remaining apps. The bedtime and app limit features work at the system level through Google Play Services.

### 3. Device Isolation

The phone has its own internet connection (mobile data or separate WiFi). The desktop DNS lockdown does not extend to it. Instead, rely on:

- ADB debloat to remove browsers and app stores (cannot install new distractions)
- Family Link to enforce limits on whatever remains
- Physical separation — phone in another room during focus blocks

If the phone supports it, a DNS-level blocker (NextDNS, AdGuard) on the device itself can mirror the desktop allowlist. This adds cross-device consistency but requires configuring the same domain allowlist in two places — acceptable for v1, worth revisiting if the phone becomes an escape route.

### 4. Recommended App Whitelist

After debloating, reinstall only what serves focused work:

- **Communication**: Signal, WhatsApp (or keep stock SMS), phone, contacts
- **Navigation**: Maps (or use a privacy-focused alternative like OsmAnd~)
- **Music/podcasts**: AntennaPod, VLC (offline media, no recommendations feed)
- **Productivity**: Simple calendar, tasks (org-mode or todo.txt compatible)
- **Utilities**: Calculator, clock, camera, file manager

The heuristic: if the app has an infinite scroll feed, algorithmic recommendations, or a "discover" tab, it probably does not belong.

### 5. Integration with Desktop System

| Desktop state | Phone expectation | Enforcement |
|---------------|------------------|-------------|
| Sealed | Phone equally restricted | Family Link bedtime + app limits, no browser |
| Locked (allowlist active) | No distraction apps | ADB debloat already done |
| Unrestricted | Normal phone use | — |

No code integration between desktop and phone for v1. The phone policy is set once via ADB + Family Link and maintained manually. If the desktop seal weakens over time (repeated unseal), the phone policy remains — it is harder to bypass than a software toggle.

### 6. Grayscale Mode — Reduce Visual Stimulus System-Wide

Android's accessibility framework includes a color correction engine that can enforce a permanent grayscale display — no third-party app needed, works even if the feature is not exposed in Settings > Accessibility.

```bash
# Enable grayscale
adb shell settings put secure accessibility_display_daltonizer_enabled 1
adb shell settings put secure accessibility_display_daltonizer 0

# Disable grayscale (restore color)
adb shell settings put secure accessibility_display_daltonizer_enabled 0
```

These settings survive reboots. The OS enforces them at the compositor level, so every app, launcher, and notification is affected. A full Settings search will not show a toggle — the only way to disable it is via ADB (or a factory reset).

---

## Extending to Other Platforms

### iOS / iPadOS

- Screen Time with content restrictions (similar to Family Link)
- MDM profile for enforced restrictions (requires Apple Configurator or third-party MDM)
- Cannot ADB-debloat — relies entirely on Screen Time + guided access
- More locked down by default, fewer bloat packages to remove
- **Verdict**: iOS is harder to lock down to the same degree; Family Link + ADB on Android is stricter

### Chromebooks

- Already lean by design
- Google Family Link extends to Chromebooks under the same account
- Limited local app ecosystem; most functionality is web-based
- **Verdict**: Already focused enough — no special treatment needed
