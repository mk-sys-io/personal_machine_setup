# Bootstrap WiFi Runbook

Read offline via `cat /mnt/ventoy/bootstrap-wifi.md` (no pager assumed).
Self-contained: every step carries its reason inline.

## Installer checklist

DO:

* Boot the installer UEFI-native (no CSM/legacy — target is UEFI-only, legacy installs don't boot).
* Configure installer WiFi and say yes to the Debian mirror (netinst needs it; without it the `standard` task installs partially).
* Enable `non-free-firmware` when offered (bookworm ships iwlwifi/realtek/mediatek/atheros/brcm80211 there).
* Select tasks `Standard system utilities` + `laptop` only (`laptop` pulls the minimal WiFi stack, no desktop/NM).
* Leave the root password **blank** (the only way d-i installs `sudo` + sudo-group membership).

DON'T:

* Set a root password (locks you out of `sudo` later — unfixable post-hoc, reinstall required).
* Select any desktop task (pulls NM early and fights the Reboot-1 stanza path below).
* Install offline / skip the mirror (same partial-`standard` failure as above).
* Assume WiFi persists — verify on first boot (installer normally propagates it, but wipe-class symptoms recur; triage below before re-entering anything).

## Reboot 1 — first boot triage

Triage before re-entering anything. The installer normally propagates
your WiFi config — check what owns the network first.

Installer `netcfg` writes `wpa-ssid/psk` and the target inherits
`interfaces` verbatim — no wipe on modern netcfg (bug #1029352, fixed
in 1.182). So triage owner first (renderer → firmware →
wpasupplicant/NM → iface name); re-enter SSID/PSK only if no owner exists.

```sh
ip a                       # find iface (wlpXs0-style, never assume wlan0)
cat /etc/network/interfaces
ls /etc/NetworkManager/system-connections/ 2>/dev/null
rfkill list
dmesg | grep -i firmware
```

* Stanza with `wpa-ssid` present → ifupdown owns it; go to "Verify".
* `system-connections/*.nmconnection` present → NetworkManager owns it; do NOT touch `interfaces`.
* Loopback-only `interfaces` and no NM profile → write the stanza (fallback below).

### Fallback: manual stanza (run once, then re-run bootstrap)

Fast path (before pressing Reboot in the installer): Alt-F2, then

```sh
cp /etc/network/interfaces /target/etc/network/interfaces
```

Alt-F1, Reboot.

Otherwise, on first login:

1. `ip a` — find iface (wlpXs0-style, never assume wlan0).
2. Write `/etc/network/interfaces` (replace iface/SSID/pass):

```sh
sudo tee /etc/network/interfaces <<'EOF'
source /etc/network/interfaces.d/*
auto lo
iface lo inet loopback
allow-hotplug <iface>
iface <iface> inet dhcp
  wpa-ssid <SSID>
  wpa-psk <PASS>
EOF
sudo chmod 600 /etc/network/interfaces   # hides PSK
```

3. Bring it up (separate steps — radio-block and DHCP fail independently):

```sh
sudo rfkill unblock wifi
```

```sh
sudo ifup <iface>
```

### Verify

First ping = routing, second = DNS (kept separate so a DNS failure can't mask working routing):

```sh
ping -c3 9.9.9.9
```

```sh
ping -c3 deb.debian.org
```

If not: `ip a; iw dev; rfkill list; dmesg | grep -i firmware`.

## Stick mount

The minimal install does not auto-mount the USB stick:

```sh
sudo mkdir -p /mnt/ventoy
```

```sh
sudo mount "$(blkid -L Ventoy)" /mnt/ventoy
```

## Stage-0

Primary path — run the bootstrap from the stick (handles mount, net
preflight, and sudo checks; invoke via `bash`, no exec bit on exFAT):

```sh
bash /mnt/ventoy/bootstrap.sh
```

Fallback — only if `bootstrap.sh` is missing or broken, network already
verified above, run these by hand (single apt invocation, then clone):

```sh
sudo apt-get update && sudo apt-get install -y \
  sudo git make python3 curl ca-certificates gnupg \
  wpasupplicant wireless-tools iw rfkill xz-utils
git clone https://github.com/mk-sys-io/personal_machine_setup.git \
  ~/linux_setup && cd ~/linux_setup && ./install.py
```

`install.py` takes it from here: `ensure_stage0()` → `load_env()` →
`packages/` converge → WiFi migration to NetworkManager (survives Reboot 2).

## Stick-builder fallback (manual layout)

If `tools/make-ventoy-stick.sh` is unavailable, the stick layout is:

1. Confirm `/dev/sdX` (wipe-confirm!) → `sudo /opt/ventoy/Ventoy2Disk.sh /dev/sdX`.
2. Verify `by-label/Ventoy`.
3. Copy the Debian netinst `.iso` to the stick.
4. Copy `tools/bootstrap.sh` → `bootstrap.sh` (stick root).
5. Copy this file → `bootstrap-wifi.md` (stick root, lowercase).
6. Verify the listing before rebooting into it.
