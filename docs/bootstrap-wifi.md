# Bootstrap WiFi Runbook

Commands only. Rationale lives in `plans/active/system-enhancement/10-bootstrap-wifi.md`.
Read offline via `cat /mnt/ventoy/bootstrap-wifi.md` (no pager assumed).

## Installer checklist

DO:

* Boot the installer UEFI-native (no CSM/legacy).
* Configure installer WiFi and say yes to the Debian mirror.
* Enable `non-free-firmware` when offered.
* Select tasks `Standard system utilities` + `laptop` only.
* Leave the root password **blank** (this installs `sudo` and puts your user in the sudo group).

DON'T:

* Set a root password (locks you out of `sudo` later — unfixable post-hoc).
* Select any desktop task.
* Install offline / skip the mirror.
* Assume WiFi persists — verify on first boot (triage below).

## Reboot 1 — first boot triage

Triage before re-entering anything. The installer normally propagates
your WiFi config — check what owns the network first.

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
sudo chmod 600 /etc/network/interfaces
```

3. Bring it up:

```sh
sudo rfkill unblock wifi
sudo ifup <iface>
```

### Verify

```sh
ping -c3 9.9.9.9 && ping -c3 deb.debian.org
```

If not: `ip a; iw dev; rfkill list; dmesg | grep -i firmware`.

## Stick mount

The minimal install does not auto-mount the USB stick:

```sh
sudo mkdir -p /mnt/ventoy
sudo mount "$(blkid -L Ventoy)" /mnt/ventoy
```

## Stage-0

With network verified, run the bootstrap (also on the stick root as `bootstrap.sh`;
invoke via `bash` — no exec bit on exFAT):

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
