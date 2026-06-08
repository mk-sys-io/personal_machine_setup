# Manual Work

Steps that cannot be automated and must be performed manually after running `install.sh`.

---

## CopyQ: Enable auto-paste on Wayland

CopyQ cannot auto-paste (Enter → paste to previous window) on Wayland because the protocol blocks synthetic keystrokes. To enable it, import the "Wayland Support" command:

1. Open CopyQ (`$mod+v` or `copyq toggle`)
2. **File → Commands → Import**
3. Wait for the command list to load
4. Search for **"Wayland Support"**
5. Click install/import

This command uses `ydotool` (installed automatically by `install.sh`, daemon started as `exec ydotoold` in Sway config) to inject keystrokes. Without it, selecting an item copies it to clipboard — paste manually with `Ctrl+V`.

**When:** Once, after first CopyQ launch.

---

## General setup

1. **Log out and back in** (or restart Sway via `$mod+Shift+e` then log in again) after `install.sh` completes — this ensures Sway reads the updated config and all services start fresh.
