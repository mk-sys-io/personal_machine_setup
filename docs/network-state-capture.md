# Network-state capture (temporary)

> **TEMPORARY.** Evidence instrumentation for one decision: are the
> Reboot-1 WiFi recovery docs still needed? Delete this file and
> `tools/capture-network-state.sh` once the decision is made. The cleanup
> checklist is at the bottom.

## Why

The GUI installer normally configures WiFi and propagates it to the target
(no NetworkManager in the minimal task set, so `/etc/network/interfaces`
gets the stanza). `install.py` later migrates that stanza to
NetworkManager. One clean install after this instrumentation lands should
show both halves of that path:

- **pre** — what the installer actually left behind;
- **post** — what step 22 actually converged to (or failed to).

The current pre-change dataset is discarded. The first fresh install after
the change is the dataset.

## Wiring

| Phase | Caller | When | Meaning |
| --- | --- | --- | --- |
| `pre` | `tools/bootstrap.sh` | after the stick mounts, before stage-0 apt | installer output; a failed net preflight is recorded before the operator can intervene |
| `post` | `lib/22-wifi-migrate.py` | after the migration attempt (`OK`/`SKIP`/`PARTIAL`/`FAIL`) | converged or broken NetworkManager state |

The post call is non-fatal: a missing script or a capture failure becomes a
warning, never a migration failure.

## Usage

```sh
bash tools/capture-network-state.sh --phase pre
bash tools/capture-network-state.sh --phase post --status OK
```

- `--phase pre|post` (default `pre`, for old stick copies).
- `--status n/a|OK|SKIP|PARTIAL|FAIL` (default `n/a`; meaningful for post).
- Unknown/malformed arguments exit 2. A successful write exits 0.
- `NETSTATE_ETC` overrides `/etc` for fixture testing only.

## Outputs

```text
~/.config/install/net-state-<host>-<phase>-<ts>.txt   (0600, per-run history)
/mnt/ventoy/net-state-<host>-<phase>-<ts>.txt         (0644, if the stick is mounted)
```

`<ts>` is UTC `YYYYMMDDTHHMMSSZ`. The stick copy keeps the timestamp on
purpose: a later reinstall must not overwrite the previous install's
evidence.

## Reading a snapshot

The last line is machine-readable:

```text
NETSTATE {"host":"...","boot_id":"...","phase":"pre","status":"n/a",
          "boot_epoch":...,"interfaces_mtime_epoch":...,
          "post_boot_edit":false,"owner":"ifupdown","iface":"wlp0s20f3",
          "ssid_src":"ifupdown","ssid_len":9,"ssid_sha8":"...",
          "driver":"iwlwifi","wifi_state":"unavailable","stanza":true,
          "nm_profiles":0,"nm_installed":false,
          "sudo":"available","partial":false}
```

| Field | Meaning |
| --- | --- |
| `phase`, `status` | which half of the run this is; 22's status for post |
| `boot_epoch` | kernel boot time from `/proc/stat` `btime` |
| `interfaces_mtime_epoch` | `/etc/network/interfaces` modification time |
| `post_boot_edit` | `true`/`false` for pre when both epochs exist; `null` otherwise. `true` means the file was written after boot, i.e. a manual fix is suspected. |
| `owner` | `nm`, `ifupdown`, `ifupdown-static` (non-loopback stanza without WPA), or `none` |
| `wifi_state` | NetworkManager's device state; `unavailable` means `nmcli` is absent, not necessarily that WiFi is down; `absent` means NM does not see the device |
| `stanza` | a WPA stanza exists in `/etc/network/interfaces` |
| `nm_profiles` | `.nmconnection` files under `/etc/NetworkManager/system-connections` |
| `nm_installed` | `network-manager` package state |
| `sudo`, `partial` | whether root-only reads succeeded; `partial=true` weakens the evidence |

### Expected pair

- **pre:** `owner=ifupdown`, `stanza=true`, `nm_installed=false`,
  `post_boot_edit=false`, `sudo=available`, `partial=false`.
- **post:** `owner=nm`, `wifi_state=connected`, `nm_profiles>=1`,
  `status=OK`.
- **Fallback used:** a broken pre snapshot (`owner=none` or
  `ifupdown-static`, `stanza=false`) followed by a fixed pre snapshot in
  the same `boot_id`, or `post_boot_edit=true`. In that case the recovery
  runbook is doing real work; keep it.

### Caveats

- Run `install.py` from the bootstrap prompt so pre and post share
  `boot_id` and the stick stays mounted. A reboot in between is usable but
  not a clean pair.
- Same hardware proves repeatability, not generality.
- A successful pair proves the normal path. It does **not** prove the
  failure path (offline WiFi, no owner, loopback-only interfaces) never
  happens; that is the path the recovery docs exist for.

## Preserving evidence

A fresh reinstall wipes `~/.config/install`. The timestamped stick copies
are the durable record. If `/mnt/ventoy` was not mounted during the post
capture, copy the local `net-state-*-post-*.txt` to the stick or another
host before reinstalling.

## Rollout for the dataset

1. Commit and push the changes (the fresh install clones from GitHub).
2. Rebuild the Ventoy stick so it carries the new `bootstrap.sh` and
   `capture-network-state.sh`.
3. Reinstall from scratch.
4. Run `bash /mnt/ventoy/bootstrap.sh`, accept the `install.py` launch
   prompt.
5. Collect `net-state-<host>-pre-<ts>.txt` and
   `net-state-<host>-post-<ts>.txt` from the stick, plus the
   `22-wifi-migrate` log line.
6. Compare and decide the recovery-docs question.

## Cleanup checklist

Delete the instrumentation:

- [ ] `rm tools/capture-network-state.sh docs/network-state-capture.md`
- [ ] `tools/bootstrap.sh`: remove the TEMPORARY capture block
      (`phase=pre`, before the net preflight).
- [ ] `tools/make-ventoy-stick.sh`: remove `tools/capture-network-state.sh`
      from the source list, the copy guard, and the verify list
      (currently around lines 366-367, 649-650, 656-658).
- [ ] `lib/22-wifi-migrate.py`: remove `CAPTURE_SCRIPT`,
      `CAPTURE_TIMEOUT`, `record_post_state()`, the `--no-capture` flag,
      the call in `main()`, and the docstring mentions.
- [ ] Remove stale `net-state-*.txt` and `capture-network-state.sh` from
      the stick (or rebuild it).

After the docs verdict (separate change):

- [ ] `tools/bootstrap.sh`: `print_wifi_help()` (currently lines 13-37)
      and the `:10` "completed Reboot-1 manual WiFi fix" wording, which
      no longer matches the GUI-installer flow.
- [ ] `install.py`: `print_wifi_help()` (currently lines 414-427) and the
      `docs/recovery/new-machine.md` hint (currently line 835).
- [ ] `lib/22-wifi-migrate.py`: `REBOOT1_DOC` and its two references, if
      the doc is retired.
- [ ] `docs/recovery/bootstrap-wifi.md`: the Reboot-1 triage/fallback/
      verify block (currently lines 23-101), keeping the installer
      checklist, stick mount, stage-0, and netcfg-bug notes.
- [ ] `docs/recovery/new-machine.md`: the "Reboot-1 WiFi" chain/section
      (currently lines 11 and 34-46).

Verify nothing is left:

```sh
rg -n 'capture-network-state|net-state|post_boot_edit|record_post_state|--no-capture'
```

## Redaction and residuals

- WPA passphrases, `psk=`, `password=`, `identity=`, and MAC addresses are
  replaced.
- Every detected SSID candidate (stanza, NM profile content, `iw dev`,
  WiFi connection name, NM profile filename stem) is replaced with
  `<ssid-masked>`.
- Still present: hostname, SSID length and `sha8`, `boot_id`, interface
  names, driver, routes, and system logs. Review a snapshot before sharing
  it widely.
- An SSID containing a newline defeats the line-based literal masking;
  length and `sha8` are still emitted. This is an accepted residual.
