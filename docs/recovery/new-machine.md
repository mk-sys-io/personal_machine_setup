# new machine — bare iron to restored workstation

## assumption (load-bearing)

The whole flow hinges on one out-of-band channel: a mobile phone with
internet and the Bitwarden vault open and synced — the **sole** owner of
the restic password (`services/restic`), B2 keyID/appKey (`services/b2`),
and the GPG passphrase. Nothing on the bootstrap path opens a URL; the
phone is the channel. Without it, `install.py` fails abort-grade at
`21-store-bootstrap` and nothing downstream converges.

Chain: stick → boot → installer → Reboot-1 WiFi → stage-0 → `./install.py` → restore.

## 1. Build the stick

`tools/make-ventoy-stick.sh` (builder-side, destructive to the USB). Lays
down: Debian netinstall ISO + `bootstrap.sh` + `bootstrap-wifi.md` +
`new-machine.md` (this file) at the Ventoy-labelled stick root. Manual
fallback: `bootstrap-wifi.md` stick-builder steps.

## 2. Boot blank iron

Insert stick → UEFI boot menu → select the Ventoy entry → pick the ISO.
First boot with Secure Boot: enroll `ENROLL_THIS_KEY_IN_MOKMANAGER.cer`
once per machine. Details vary by vendor; the script comments are the
reference.

## 3. Debian netinstall

Checklist (`bootstrap-wifi.md`): UEFI-native install, installer WiFi +
mirror yes, `non-free-firmware` enabled, tasksel `Standard` + `laptop`
only, root password blank. Installer defaults for the rest; non-standard
layouts: `../disk-tools.md`.

## 4. Reboot-1 WiFi, then stage-0

Sub-chain (`bootstrap-wifi.md` sections, linked not duplicated):

- Triage owner first (ifupdown stanza vs NM profile vs none).
- Fallback manual stanza (or Alt-F2 pre-reboot fast path).
- Dual-ping verify (routing vs DNS split).
- Stick mount (`/mnt/ventoy`), then:

```sh
bash /mnt/ventoy/bootstrap.sh   # stage-0: net preflight, apt set, clone + ./install.py prereqs
./install.py                    # full converge
```

## 5. Restore order

Reboot → verify-stable → trunk step `21-store-bootstrap` runs inside
`install.py` (vault stream, automatic; idempotent SKIP, abort-grade FAIL;
prompts the Bitwarden-held password + GPG passphrase on `/dev/tty` — see
assumption above) → livedata standalone:

```sh
python3 lib/40-restore.py --preview-only   # stage + preview, move nothing in
python3 lib/40-restore.py                  # move in knowledge_base, Videos/keep, .ssh, .gnupg
pi-setup auth/probe/fetch                  # regenerate curated/ (never restored)
```

Runbook: `backup.md`. Secrets/custody: `../gopass.md`.
