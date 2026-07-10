# linux_setup Rework — Issues

---

## 1. Flat repo structure

**Problem**. `.config/` mixes dotfiles (sway, waybar, foot), dev tools (zed, opencode), and lockdown system (allowlist, nftables, sudoers) in one directory with no grouping.

**Solution**. Split into three top-level directories: `dotfiles/`, `dev/`, `lockdown/`. Each group has its own `Makefile` targets and deployment logic.

---

## 2. Visual design is hardcoded and not swappable

**Problem**. Theme colors/fonts/spacing are hardcoded into every config file. Importing a ready-made dotfiles theme requires editing 8+ files manually.

**Solution**. Extract all visual values into `dotfiles/themes/<name>/theme.env`. App configs become templates with `@VARIABLE@` placeholders. `make theme NAME=dracula` substitutes and redeploys all visuals in one command.

---

## 3. Lockdown system depends on specific dotfile components

**Problem**. `seal_lib.py` directly calls `copyq clear`, `wl-copy --clear`, and scans for `sway`/`waybar`/`copyq` processes by name. Swapping any of these breaks seal/unseal silently.

**Solution**. Create an abstraction layer at `lockdown/lib/` (clipboard-clear.sh, discover-session.py, terminal wrapper). Lockdown calls these adapters only. Swapping a component = editing one adapter script, not lockdown internals.

---

## 4. Hardcoded paths and usernames scattered everywhere

**Problem**. `/home/mike/.opencode/bin/opencode`, username `mike`, UID `1000` hardcoded in 17+ locations across `enter-internet-netns`, `seal_lib.py`, `sudoers/`, `polkit/`, `bashrc`, `install.sh`. Changing any of these requires editing multiple files — some deep in the lockdown system.

**Solution**. Single `lockdown/config.env` as source of truth (`USERNAME`, `OPENCODE_PATH`, `TERMINAL`, `WALLPAPER`). All templates use `{{USER}}`. All lockdown scripts source `config.env` at runtime.

---

## 5. install.sh is a 661-line monolithic script

**Problem**. Single script with `set -euo pipefail` — crashes on first error with no log, no partial-success summary, no way to resume. Adding anything requires editing this one fragile script.

**Solution**. Factor into a `Makefile` with independent targets (`make packages`, `make dotfiles`, `make lockdown`, `make dev`). Each step logs output and outcome independently. Summary at end shows pass/fail per step. Resume with `make <failed-target>`.

---

## 6. No declarative package inventory

**Problem**. Adding a new tool means editing install.sh's apt list or writing a custom install block inline. The 661-line script is the only reference for what the system installs.

**Solution**. Group packages by install method in the Makefile (`APT_PACKAGES`, `GO_INSTALLS`, `GITHUB_DEBS`, `CURL_BASH`, `GITHUB_BINS`, `CARGO_BUILDS`, `APT_REPO_PKGS`). One generic recipe per method, one list per method. Adding a tool = one entry in the right list. `packages/groups/*.txt` define logical groups (dotfiles, lockdown, dev, worktools, browsers).

---

## 7. No error logging or resume capability

**Problem**. No record of what succeeded or failed. Must re-run the entire 661-line script after fixing any error.

**Solution**. A `$(call step, ...)` Makefile wrapper that logs every command, captures exit codes, appends to a summary file, and continues to the next step. Produces a report at the end.

---

## 8. Background image not supported

**Problem**. Sway config has `output * bg #1e1e2e solid_color` — flat color only. No wallpaper mechanism exists.

**Solution**. Add `dotfiles/background.jpg` (default) with per-theme override at `dotfiles/themes/<name>/background.jpg`. Sway template substitutes `@WALLPAPER@`.

---

## 9. Terminal hardcoded in utility scripts

**Problem**. `toggle-nmtui.sh` calls `foot nmtui` directly. Swapping the terminal breaks WiFi toggle and any other script that launches a terminal.

**Solution**. Lockdown adapter `lockdown/lib/terminal` (installed to `/usr/local/bin/terminal`). All scripts call `terminal` instead of a specific terminal binary. `lockdown/config.env` sets `TERMINAL=foot`.

---

## 10. No theme import mechanism

**Problem**. sway-setup (codeberg.org/justaguylinux/sway-setup) and other public dotfiles repos ship with polished, designer-made themes. No way to import their color values into this system without manual copy-paste across 8 config files.

**Solution**. `tools/import-sway-setup.sh` reads sway-setup theme files, maps variables to `theme.env` format. `tools/import-theme.sh` is a generic importer for any dotfiles repo. Both output to `dotfiles/themes/<name>/`.

---

## 11. NVIDIA approach incompatibility with sway-setup

**Problem**. sway-setup's nvidia-setup.sh uses `nvidia_drm.modeset=1` and runs sway with `--unsupported-gpu`. This machine (RTX 3050 laptop, display wired to Intel iGPU only) already tried this approach (see `docs/nvidia-attempt.md`) — it crashed sway irrecoverably.

**Solution**. Preserve the compute-only approach from `docs/nvidia-setup.md`: block `nvidia-drm` and `nvidia-modeset` at modprobe level, load only `nvidia.ko` and `nvidia-uvm.ko` for CUDA/NVENC. Import sway-setup's themes only — not its NVIDIA script. The abstraction layer (`lockdown/lib/clipboard-clear.sh`, `discover-session.py`, etc.) makes it safe to borrow themes from any external repo without pulling in incompatible system-level changes.
