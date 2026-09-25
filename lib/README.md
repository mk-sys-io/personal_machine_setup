# lib — install step modules (directly callable only)

**Model:** if you can run it as `bash lib/X`, `python3 lib/X`, or
`./install.py --only X`, it lives here. Everything else — sourced,
executed-by-path, or imported — lives in [`helpers/`](helpers/README.md).

- `NN-*` prefix = trunk-dispatched step (`install.py` `step_table()`).
  Bash steps source `helpers/common.sh`; Python steps mirror env via the
  `load_env` import. Exit codes: 0 = converged, 2 = skip, 3 = partial,
  else fail. Bash steps may print one `RESULT {json}` trailer.
- `65-vm.py` = standalone entry point (`sudo python3 lib/65-vm.py
  [--force]`), driven by `tools/vm.py`. Never trunk-wired: it exit-2s
  inside any VM by design, which is where the final gate runs.

| Module | Runtime | Dispatched by |
|---|---|---|
| `05-home-skeleton.sh` | bash | install.py |
| `20-packages.py` | python3 | install.py |
| `22-wifi-migrate.sh` | bash | install.py |
| `25-searxng.py` | python3 | install.py |
| `30-hardware.sh` | bash | install.py |
| `35-nvidia.sh` | bash | install.py |
| `40-system_config.sh` | bash | install.py |
| `45-brave.py` | python3 | install.py |
| `50-github_setup.sh` | bash | install.py |
| `55-security.sh` | bash | install.py |
| `60-ark.sh` | bash (root) | install.py |
| `65-vm.py` | python3 (root) | manual + `tools/vm.py` |

Superseded modules are archived under `plans/archive/system/`, never
left beside their replacements. The frozen `install.sh` at repo root
references pre-move paths; it never runs again (§20 archive gate).
