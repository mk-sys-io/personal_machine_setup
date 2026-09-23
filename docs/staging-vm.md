# Staging VM

A disposable QEMU/KVM replica of this workstation for testing repo changes
before they touch the live machine. Every change the repo makes — dotfiles,
system configs, Ark, networking — is exercised in the VM first. The harness
(`vm`) only drives the VM; provisioning is a manual step inside it.

## Quick start

```sh
vm build          # build the golden base image (one-time, sudo)
vm boot           # boot the single overlay (fresh from golden, or resume)
ssh vm            # log in (user: vm, password: 0000)
vm view           # open the Sway desktop in a SPICE window
vm shutdown       # graceful power-off (overlay preserved)
vm destroy        # tear down + delete all overlays
```

First-time setup: `make all` installs the tooling (qemu, libvirt, virt-viewer)
and `vm build` adds your user to the `libvirt` group (log out/in once).

## Major components

| Component | What it does |
|---|---|
| **Golden base** | Frozen trixie rootfs (`golden.qcow2`, read-only) built by `vm build` via `lib/65-vm.sh`. The baseline every run starts from. |
| **Overlay chain** | One linear chain: `golden.qcow2 → <base overlay> → <snapshot>.qcow2`. `vm boot [NAME]` creates the base overlay (default `staging-vm`); `vm snapshot NAME` checkpoints it; `vm rollback NAME` restores a checkpoint point-in-time (native libvirt revert — the snapshot stays reusable). |
| **virtiofs Path A** | Host repo mounted read-only at `/home/vm/linux_setup` — uncommitted host edits appear live in the VM. |
| **git clone Path B** | VM's own clone at `/home/vm/linux_setup-git`, reset to a branch tip by `vm sync --branch X` — tests exactly what would merge. |
| **SPICE view** | `vm view` opens the desktop via virt-viewer (single window per VM — SPICE is single-client). |
| **ssh** | `vm boot` writes a `Host vm` entry to `~/.ssh/config`; `ssh vm` gives a terminal. |

## Daily usage

| Command | What it does |
|---|---|
| `vm build [--force]` | Build/rebuild the golden base (sudo). Rebuild drops the overlay chain only after the swap succeeds. |
| `vm boot [NAME]` | Boot the single overlay — resume if shut off, else create fresh from golden (NAME defaults to `staging-vm`). |
| `vm view` | Open the desktop window (auto-boots the VM). |
| `vm shutdown [--force]` | Graceful ACPI power-off, overlay preserved. |
| `vm sync --branch X` | Reset the Path B clone to `origin/X` (auto-boots the VM). |
| `vm snapshot NAME` | Checkpoint the current state (filesystem-consistent via qemu-guest-agent). |
| `vm snapshot list` | List available snapshots (restore points) + the current one. |
| `vm snapshot delete NAME` | Delete a snapshot (leaf only; VM must be shut off). |
| `vm rollback NAME` | Restore a snapshot point-in-time (revert; snapshot stays reusable). |
| `vm status` | Golden/overlay/snapshot/libvirt/ssh state. |
| `vm destroy` | Destroy the VM and delete the whole overlay chain. |

**Single overlay.** The harness runs exactly one overlay at a time — a single
linear chain (`golden → base → snapshots`) behind one domain. `vm boot` is
create-or-resume on that one overlay: it creates a fresh base overlay from the
golden when none exists, and resumes the existing one when the VM is shut off.
`vm boot NAME` names the base overlay (default `staging-vm`); the name only
applies to fresh creation. `vm destroy` wipes the whole chain regardless of
name.

## Branch workflow

1. On the host: `git checkout -b staging/<feature>`, commit, push.
2. `vm boot` → `vm sync --branch staging/<feature>` (Path B) — or just use the
   live ro mount (Path A) for uncommitted iteration.
3. `ssh vm` → `cd /home/vm/linux_setup && ./install.sh` (manual provisioning).
4. On success: `vm snapshot provisioned-<feature>` for a fast re-run.

## What's skipped / not synced

- **SKIP modules in the VM:** `30-hardware.sh`, `35-nvidia.sh`, `65-vm.sh`
  exit 2 (SKIP) inside a VM — host hardware (backlight/wifi/btusb/NVIDIA) and
  nested VM builds don't apply. Expected, not failures.
- **Not synced:** the `services/github` gopass token can't
  travel through git; it's provisioned out-of-band in the VM.
  (`config.txt` is committed and syncs normally.)

## Rollback

`vm snapshot pre` before a risky change, then `vm rollback pre` to undo it.
Rollback uses libvirt's native external-snapshot revert: the VM boots a fresh
overlay on the snapshot's point-in-time state (the experiment's writes are
discarded), and the snapshot **stays in the list** — you can roll back to it
again, or `vm snapshot delete pre` once you're done with it (shut the VM off
first). `vm snapshot list` shows what restore points exist. `vm destroy` wipes
everything back to the golden base.

Snapshots are filesystem-consistent (`--quiesce` via qemu-guest-agent, which
ships in the golden). If you're on an older golden without the agent, use
`vm snapshot NAME --no-quiesce`.

## Pitfalls

- **`./install.sh` on the bare VM is known-broken.** It's hardcoded to the
   host: it uses the host's committed `config.txt` through
  the ro mount (`USERNAME=mike`, `/home/mike/*` paths break `make dotfiles`
  and Ark's `getpwnam("mike")`). The fix is the install.py migration, not
  harness patching — the VM panel works standalone (boot/status/destroy + ssh).
- **The mounted repo is read-only.** Only `/home/vm/linux_setup` is `ro`
  (virtiofs `-o ro`); the rest of the VM's home is writable. Edit the host
  original — changes appear in the VM live; the VM can't write back.