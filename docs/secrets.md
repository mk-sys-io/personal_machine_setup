# Secrets

Policy: zero secrets in GNOME Keyring (Option C, 2026-09-30). Silence via
blank `Default`, not encryption. Pinned in `packages/apt.txt`,
`lib/40-system_config.sh`, `dotfiles/sway/scripts/autostart.sh`.

## Where secrets live

| Secret | Home |
|---|---|
| Utility API keys/tokens | gopass (offline, GPG-encrypted, per-request reads) |
| Website credentials | Bitwarden (cloud vault + encrypted local cache) |
| Wi-Fi/VPN PSKs | Out of scope (shared access, not owned infra) |
| Browsers | Managers disabled; profiles hold nothing (throwaway Safe Storage keys) |
| Zed | Offline: full editor, own/gateway/local models. Sign-in adds only: realtime collaboration, hosted models/billing, unlimited Edit Predictions — at the cost of persisting session + provider tokens in the keyring |

## Threat model

- Encrypted keyring defends offline disk access (theft, live-USB, forensics, backups, other users). Login password alone encrypts nothing.
- Useless against same-UID malware post-unlock, root, keyloggers. Same as Windows DPAPI / macOS Keychain, less integrated on Linux.
- With zero stored secrets, ciphertext protects nothing. FDE + login password are the real defense.

## Why keyring is unused

Sway ignores xdg-autostart; PAM unlocks only a collection named `login`;
Zed (`oo7`) unlocks on every launch even logged out (zed#17460). Blank
`Default` ends the loop with nothing to lose.

## Repeat on a new machine

1. `install.py` + `make dotfiles dev`
2. Delete any existing `Default` (seahorse, or `rm ~/.local/share/keyrings/*`):
   no backup needed — under this policy it holds only auto-regenerating
   Safe Storage keys. (If deviating from the policy and unsure, back up first.)
3. Launch Zed → set-password prompt → leave EMPTY + confirm
4. Reboot → login → launch Zed/Brave/Chrome via rofi → no prompt
5. Verify: `Default` unlocked without typing; no `Prompt was dismissed` in Zed log

## Revisit trigger

First saved browser password OR Zed sign-in → abandon C: create `login`
(password == login password), migrate, delete `Default`, wire manual
`pam_gnome_keyring.so` lines in `/etc/pam.d/login` (recipe in code comments).
