# Timeshift Cheatsheet

## Creating Snapshots

| Command | When |
|---------|------|
| `sudo timeshift --create --comments "before-nextdns"` | Before major system changes (network, packages, DNS) |
| `sudo timeshift --create --comments "before-browser-config"` | Before modifying browser policies |
| `sudo timeshift --create --tags D` | Create a daily snapshot manually |

## Listing & Inspecting

| Command | When |
|---------|------|
| `sudo timeshift --list` | List all available snapshots |
| `sudo timeshift --list-snapshots` | Show snapshot details (dates, tags) |

## Deleting Snapshots

| Command | When |
|---------|------|
| `sudo timeshift --delete` | Delete a specific snapshot (interactive, pick from list) |

## Restoring

| Command | When |
|---------|------|
| `sudo timeshift --restore` | Interactive restore — pick a snapshot to roll back to |
| `sudo timeshift --restore --snapshot-device /dev/nvme0n1p5` | Restore specifying the device directly |

## Configuration

| Command | When |
|---------|------|
| `sudo timeshift --list-devices` | Show available devices for snapshots |
| `sudo timeshift --check` | Check if scheduled snapshots are working |

## Schedule Settings

Snapshots are stored in `/timeshift/` on the root partition (`nvme0n1p5`). Default schedules:
- **Daily** — keep 5
- **Weekly** — keep 3
- **Monthly** — keep 2

Adjust via `sudo timeshift-gtk` or by editing `/etc/timeshift/timeshift.json`.
