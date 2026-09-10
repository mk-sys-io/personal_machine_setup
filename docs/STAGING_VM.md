# Staging VM

A disposable QEMU/KVM replica of this workstation for testing repo changes
before they touch the live machine. Every change the repo makes — dotfiles,
system configs, Ark, networking — is exercised in the VM first. The harness
(`vm`) only drives the VM; provisioning is a manual step inside it.

## Quick start

```sh
vm build          # build the golden base image (one-time, sudo)
vm up             # boot the VM (fresh overlay from golden)
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
| **Overlay chain** | `golden.qcow2 → staging-vm.qcow2 → <snapshot>.qcow2`. `vm up` boots a fresh overlay; `vm snapshot NAME` checkpoints it; `vm rollback NAME` boots a fresh overlay on a checkpoint. |
| **virtiofs Path A** | Host repo mounted read-only at `/home/vm/linux_setup` — uncommitted host edits appear live in the VM. |
| **git clone Path B** | VM's own clone at `/home/vm/linux_setup-git`, reset to a branch tip by `vm sync --branch X` — tests exactly what would merge. |
| **SPICE view** | `vm view` opens the desktop via virt-viewer (single window per VM — SPICE is single-client). |
| **ssh** | `vm up` writes a `Host vm` entry to `~/.ssh/config`; `ssh vm` gives a terminal. |

## Daily usage

| Command | What it does |
|---|---|
| `vm build [--force]` | Build/rebuild the golden base (sudo). Rebuild drops the overlay chain only after the swap succeeds. |
| `vm up` | Boot the VM — resume if shut off, else fresh overlay from golden. |
| `vm view` | Open the desktop window (auto-boots the VM). |
| `vm shutdown [--force]` | Graceful ACPI power-off, overlay preserved. |
| `vm sync --branch X` | Reset the Path B clone to `origin/X` (auto-boots the VM). |
| `vm snapshot NAME` | Checkpoint the current state. |
| `vm rollback NAME` | Boot a fresh overlay on a checkpoint. |
| `vm status` | Golden/overlay/snapshot/libvirt/ssh state. |
| `vm destroy` | Destroy the VM and delete the whole overlay chain. |

## Branch workflow

1. On the host: `git checkout -b staging/<feature>`, commit, push.
2. `vm up` → `vm sync --branch staging/<feature>` (Path B) — or just use the
   live ro mount (Path A) for uncommitted iteration.
3. `ssh vm` → `cd /home/vm/linux_setup && ./install.sh` (manual provisioning).
4. On success: `vm snapshot provisioned-<feature>` for a fast re-run.

## What's skipped / not synced

- **SKIP modules in the VM:** `30-hardware.sh`, `35-nvidia.sh`, `65-vm.sh`
  exit 2 (SKIP) inside a VM — host hardware (backlight/wifi/btusb/NVIDIA) and
  nested VM builds don't apply. Expected, not failures.
- **Not synced:** gitignored secrets (`config.env`, `dev/github.env`) can't
  travel through git; they're provisioned out-of-band in the VM.

## Rollback

`vm snapshot pre` before a risky change, then `vm rollback pre` to undo it —
the VM boots a fresh overlay on the checkpoint. `vm destroy` wipes everything
back to the golden base.

## Pitfalls

- **`./install.sh` on the bare VM is known-broken.** It's hardcoded to the
  host: it sources the host's gitignored `config.env`/`dev/github.env` through
  the ro mount (`USERNAME=mike`, `/home/mike/*` paths break `make dotfiles`
  and Ark's `getpwnam("mike")`). The fix is the install.py migration, not
  harness patching — the VM panel works standalone (up/status/destroy + ssh).
- **The mounted repo is read-only.** Only `/home/vm/linux_setup` is `ro`
  (virtiofs `-o ro`); the rest of the VM's home is writable. Edit the host
  original — changes appear in the VM live; the VM can't write back.