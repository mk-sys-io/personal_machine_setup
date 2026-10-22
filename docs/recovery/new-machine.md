# new machine — bare iron to restored workstation

## assumption (load-bearing)

The whole flow hinges on one out-of-band channel: a mobile phone with
internet and the Bitwarden vault open and synced — the **sole** owner of
the restic password and B2 keyID/appKey. Nothing on the bootstrap path
opens a URL; the phone is the channel. Without it, `install.py` fails
abort-grade at the first secret prompt and nothing downstream converges.

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
bash /mnt/ventoy/bootstrap.sh   # stage-0: net preflight, apt set, clone; prompts to launch ./install.py
./install.py                    # full converge (if skipped at the prompt)
```

`./install.py` **requires a terminal** and refuses to start without one —
piping it, `nohup`, cron, or `echo | ./install.py` all exit non-zero before
any work happens. This is by design: the run prompts for the sudo ticket
and orphan-dotfile cleanup, and each prompt is drawn on `/dev/tty`.
There is no non-interactive mode and no
`YES=1` auto-approve; the old one existed only to hide a bug and was
removed. Run it from an interactive shell on the machine.

## 5. Restore order

Reboot → verify-stable → livedata standalone restore (interactive;
prompts on `/dev/tty` for the Bitwarden-held restic password and B2
credentials — see assumption above):

```sh
python3 lib/40-restore.py --preview-only   # stage + preview, move nothing in
python3 lib/40-restore.py                  # move in knowledge_base, Videos/keep, .ssh
```

Runbook + secrets/custody: `backup.md`.
