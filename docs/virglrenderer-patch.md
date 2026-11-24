# virglrenderer cursor-fix runbook

**Status:** ACTIVE
**Revisit cadence:** monthly, first Monday
**Slide rule:** if no upstream tag newer than `1.3.0` by 2027-02-10, switch to quarterly
**Owner:** linux_setup staging VM harness

## Root cause

The guest mouse pointer renders upside-down when the VM uses VirGL
(`accel3d='yes'` + SPICE `gl='yes'`). The flip happens in **QEMU's process on
the host**: `virgl_renderer_get_cursor_data()` (in `libvirglrenderer`) returns
cursor pixels with the row order unconditionally Y-flipped. The desktop
framebuffer travels a different path (scanout/display channel) and is unaffected,
so only the pointer rotates.

- Upstream bug: virglrenderer MR !1611 / commit `4e19e9992bc2fe8686fe844666626e03d8ec6e61` (merged 2026-04-15). Fix: flip only when `res->y_0_top` is set.
- QEMU issue: #2315 ("Mouse cursor is flipped / inverted / upside-down with
  virtio-gpu in some Wayland compositors") — closed 2026-07-08.
- KDE bug: #485827 — fixed in kwin 6.0.5 (dumb buffers); the virglrenderer
  fix is independent of that.
- The fix is **not** in upstream release `1.3.0` (tagged 2026-02-10, two
  months before the fix) and not in any Debian package: trixie ships
  `1.1.0-2`, unstable/testing ship `1.3.0-1` built from the pre-fix tarball.

## AppArmor confinement (why the fix didn't take effect)

libvirt confines QEMU under a per-VM AppArmor profile
(`/etc/apparmor.d/libvirt/libvirt-<uuid>`). That profile includes
`<abstractions/libvirt-qemu>` → `<abstractions/base>`, and `base` grants
`mr` only under `/lib` and `/usr/lib` — there is no rule for
`/usr/local/lib/...`. So `ld.so`'s first cache candidate
(`/usr/local/lib/x86_64-linux-gnu/libvirglrenderer.so.1`) returns EACCES and
it silently falls back to `/lib/x86_64-linux-gnu/libvirglrenderer.so.1` (the
system 1.9.0 lib). Result: the host patch is installed but never loaded.

Symptom: `ldd` (unconfined) resolves `/usr/local/...`; the confined QEMU
(`sudo grep ... /proc/<pid>/maps`) resolves `/usr/lib/...`. Verified
2026-11-05.

Fix: `system/apparmor.d/local/abstractions/libvirt-qemu` grants `mr` to
`/usr/local/lib{,32,64}/**.so*` and `/usr/local/lib/@{multiarch}/**.so*`
only (least privilege). The abstraction ends with
`include if exists <local/abstractions/libvirt-qemu>`, so the fragment is
picked up by libvirt on the next VM start without any manual
`apparmor_parser` run. Deployed by `lib/65-vm.py`
`ensure_apparmor_libvirt_local()`.

## What we did

Patched the host's `libvirglrenderer` from upstream commit `4e19e999` and
installed it into `/usr/local` (which shadows the system package via
`/etc/ld.so.conf.d`). VirGL stays enabled in `vm/domain.xml.template`.
Implemented in `lib/65-vm.py` as `ensure_virgl_patched()` (host-only, root,
converges via a marker file). Build dependencies live in `packages/apt.txt`
(TEMPORARY block) so there is a single inventory of record.

## Disposition table

| Component | Location | Today | Fix in any upstream release | Fix in trixie by default |
|---|---|---|---|---|
| Patched lib + marker | `/usr/local/lib/libvirglrenderer*`, `/usr/local/share/virglrenderer-patch-commit` | **KEEP** | **KEEP** | **DELETE** (`rm … && ldconfig`) |
| Build convergence | `lib/65-vm.py` `ensure_virgl_patched()` + `--rebuild-lib` | **KEEP** | **KEEP** | **DELETE** (function + call site) |
| Build-dep block | `packages/apt.txt` TEMPORARY block | **KEEP** | **KEEP** | **DELETE** (after promotion check) |
| Runbook (this doc) | `docs/virglrenderer-patch.md` | **KEEP** | **UPDATE** status line | **DELETE** (archive under `plans/archive/system/`) |
| VM display docs | `docs/staging-vm.md` cursor paragraph | **KEEP** (pointer) | **UPDATE** ("no action needed") | **DELETE** pointer |
| AppArmor fragment | `system/apparmor.d/local/abstractions/libvirt-qemu` (+ live `/etc/apparmor.d/local/abstractions/libvirt-qemu`) | **KEEP** | **KEEP** | **DELETE** (delete repo file + `rm` live copy) |
| VM display template | `vm/domain.xml.template` (VirGL on) | **KEEP** | **KEEP** | **KEEP** (VirGL stays on) |

## Revisit checklist (run monthly, first Monday)

```bash
# 1. Upstream tags — anything newer than 1.3.0?
curl -fsS 'https://gitlab.freedesktop.org/api/v4/projects/virgl%2Fvirglrenderer/repository/tags?per_page=100' \
  | jq -r '.[].name' | sort -V | tail -8

# 2. Upstream release-prep MRs
curl -fsS 'https://gitlab.freedesktop.org/api/v4/projects/virgl%2Fvirglrenderer/merge_requests?state=merged&per_page=20&search=release' \
  | jq -r '.[] | [.merged_at,.title] | @tsv'

# 3. Debian tracker versions
curl -fsS https://tracker.debian.org/pkg/virglrenderer | grep -oE '1\.[0-9]+\.[0-9]+-[0-9]+' | sort -u

# 4. Debian BTS (currently empty)
curl -fsS 'https://bugs.debian.org/cgi-bin/pkgreport.cgi?src=virglrenderer' | grep -i 'No reports found'

# 5. Salsa patch queue
curl -fsS 'https://salsa.debian.org/api/v4/projects/debian%2Fvirglrenderer/repository/files/debian%2Fpatches%2Fseries?ref=master' \
  | jq -r '.content' | base64 -d
```

**Slide rule:** if no upstream tag newer than `1.3.0` by **2027-02-10**, treat the cadence as slipped and switch to quarterly.

**Decision branches:**

- Upstream tag `> 1.3.0` containing `4e19e999` → Debian will package it; skip to branch 2.
- `dpkg -l libvirglrenderer1` shows a trixie version whose changelog contains `4e19e999` → proceed to §removal.

## Removal procedure (when trixie ships the fix by default)

1. Verify: `dpkg -l libvirglrenderer1` version + changelog references `4e19e999` (version bump alone is not proof).
2. Run the promotion check (§promotion) on the TEMPORARY apt.txt block — promote any newly-owned deps first.
3. Remove the patched lib:

   ```sh
   sudo rm -f /usr/local/lib/x86_64-linux-gnu/libvirglrenderer* /usr/local/share/virglrenderer-patch-commit
   sudo ldconfig
   sudo rm -f /etc/apparmor.d/local/abstractions/libvirt-qemu
   ```

4. Delete `ensure_virgl_patched()` and its call site + `--rebuild-lib` flag from `lib/65-vm.py`.
4b. Delete `ensure_apparmor_libvirt_local()` and its call site (and the deployed file) from `lib/65-vm.py`.
5. Delete the TEMPORARY block from `packages/apt.txt`.
6. Delete this runbook (archive under `plans/archive/system/` per repo convention) and the pointer in `docs/staging-vm.md`.
7. Verify: `ldconfig -p | grep virglrenderer` shows only `/usr/lib/…`; `vm shutdown && vm boot`; `vm view` → cursor upright, no glitching.

## Promotion rule

A dependency in the TEMPORARY apt.txt block (or any other temporary source) is
**promoted** to a permanent inventory section when a verified consumer outside
the virgl-patch path (`lib/65-vm.py`, this runbook) starts depending on it.

1. **Trigger:** repo evidence — a reference in `lib/`, `tools/`, `packages/*.txt`,
   `Makefile`, or a shipped script, beyond the virgl-patch context. Presence on
   disk alone does NOT qualify (that is the drift this rule prevents).
2. **Detector (part of the monthly revisit):**

   ```sh
   grep -rn --exclude-dir=.git -lE \
     '\bmeson\b|libdrm|libepoxy|libgbm|libva-|libvulkan|python3-setuptools|python3-yaml' \
     lib/ tools/ packages/ Makefile \
     | grep -vE '65-vm\.py|virglrenderer-patch\.md'
   # No output → block is still purely virgl-owned → deletion eligible.
   ```

3. **On hit — promote, don't share.** Move the line out of the TEMPORARY block
   into the appropriate permanent section with a consumer comment (mirroring the
   `libpipewire`/`libclang` precedent in `apt.txt`), and append an entry here
   with date + dep + consumer.
4. **No demotion.** Once promoted, a package stays permanent until a separate,
   evidenced removal decision.
5. **Removal-time interaction.** §removal deletes only lines still solely owned
   by the virgl path — promoted lines are excluded by construction (branch on
   the detector above, not on memory).

## Event log

| Date | Event |
|---|---|
| 2026-11-05 | Created runbook; TEMPORARY block added to apt.txt; `ensure_virgl_patched()` added to 65-vm.py. |
| 2026-11-05 | Reverted ineffective guest env-var delivery (`dotfiles/environment.d/wlr.conf`, `dotfiles/bashrc` conditional). |
