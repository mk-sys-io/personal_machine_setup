# External Device Integration

## Overview

The desktop lockdown is only half the picture. If the phone still has unrestricted access to browsers, social media, YouTube, and app stores, it becomes the path of least resistance the moment the desktop is sealed. A credible behavioral infrastructure must cover every screen.

This document covers Android integration. The goal is not to eliminate the phone but to make it a focused tool — calls, messages, maps, camera, essentials — rather than a pocket-sized internet escape hatch.

---

## Android Setup

### Prerequisites

- Android device with USB debugging enabled
- `adb` and `fastboot` from `android-sdk-platform-tools` on the desktop
- Google account with Family Link setup ability
- USB cable

Install platform tools:

```bash
sudo apt install android-sdk-platform-tools
```

### 1. ADB Debloat — Remove Distractions at Package Level

Debloating uninstalls packages for the current user (not root, no device unlock needed). These survive reboots and factory resets only undo them.

**Safety**: This only removes packages for user `0` (the primary profile). The packages remain installed in the system partition — a factory reset restores everything. Nothing is brickable.

**Commands**:

```bash
adb shell pm list packages                          # list all installed
adb shell pm uninstall -k --user 0 <package.name>   # remove for current user
```

Before running bulk uninstalls, check what's on the device:

```bash
adb shell pm list packages | sort > installed-packages.txt
```

Transfer to desktop and review before pruning.

**Common packages to remove** (varies by manufacturer):

| Category | Packages |
|----------|----------|
| Browser | `com.android.chrome`, `com.brave.browser`, `com.browser.default` |
| App store | `com.android.vending` (Google Play — prevents reinstalls) |
| Social | `com.facebook.katana`, `com.facebook.orca`, `com.instagram.android` |
| YouTube | `com.google.android.youtube`, `com.google.android.apps.youtube.music` |
| Google bloat | `com.google.android.apps.photos`, `com.google.android.apps.maps` (keep if needed), `com.google.android.apps.docs`, `com.google.android.apps.magazines` |
| Manufacturer bloat | Manufacturer-specific apps, game launchers, browser duplicates |

**Recommended minimum safe list** (keep these):

```
com.android.phone              # Phone app
com.android.dialer             # Dialer
com.android.contacts           # Contacts
com.google.android.apps.messaging  # SMS (or keep stock messaging)
com.android.settings           # Settings
com.google.android.gms         # Google Play Services (required for many things)
com.android.systemui           # System UI
com.google.android.inputmethod.latin  # Gboard (or keep stock keyboard)
com.android.providers.downloads  # Download manager
com.android.documentsui        # Files app
```

**Debloat script pattern** — grouped by category for clarity:

```bash
# browsers
adb shell pm uninstall -k --user 0 com.android.chrome || true
adb shell pm uninstall -k --user 0 com.brave.browser || true

# social media
adb shell pm uninstall -k --user 0 com.instagram.android || true
adb shell pm uninstall -k --user 0 com.facebook.katana || true

# video
adb shell pm uninstall -k --user 0 com.google.android.youtube || true

# app store (after installing all needed apps)
adb shell pm uninstall -k --user 0 com.android.vending || true
```

After debloating, lock USB debugging off in Developer Options so re-enabling requires physical access to the device.

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
