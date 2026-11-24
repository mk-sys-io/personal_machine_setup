# lib — install step modules (directly callable only)

**Model:** if you can run it as `bash lib/X`, `python3 lib/X`, or
`./install.py --only X`, it lives here. Everything else — sourced,
 executed-by-path, or imported — lives in [`helpers/`](helpers/README.md).
 Consumers are not limited to `lib/` steps: the Makefile and `tools/`
 entry points consume helpers by path.

- `NN-*` prefix = trunk-dispatched step (`install.py` `step_table()`).
  Bash steps source `helpers/common.sh`; Python steps mirror env via the
  `load_env` import. Exit codes: 0 = converged, 2 = skip, 3 = partial,
  else fail. Any step may print one `RESULT {json}` trailer.
- `65-vm.py` = standalone entry point (`sudo python3 lib/65-vm.py
  [--force]`), driven by `tools/vm.py`. Never trunk-wired: it exit-2s
  inside any VM by design, which is where the final gate runs.

| Module | Runtime | Dispatched by |
|---|---|---|
| `05-home-skeleton.sh` | bash | install.py |
| `20-packages.py` | python3 | install.py |
| `22-wifi-migrate.py` | python3 | install.py |
| `30-hardware.sh` | bash | install.py |
| `35-nvidia.sh` | bash | install.py |
| `40-system_config.sh` | bash | install.py |
| `45-brave.py` | python3 | install.py |
| `50-github_setup.sh` | bash | install.py |
| `55-security.sh` | bash | install.py |
| `57-root-account.sh` | bash | install.py |
| `60-ark-deploy.py` | python3 | install.py |
| `61-ark-policy.py` | python3 | install.py |
| `63-mdm-server.py` | python3 | install.py |
| `64-mdm-tls.py` | python3 | install.py |
| `66-mdm-baseline.py` | python3 | install.py |
| `67-mdm-provision.py` | python3 | install.py |
| `68-mdm-lan.py` | python3 | install.py |
| `65-vm.py` | python3 (root) | manual + `tools/vm.py` |

REST envelope contract (`67-mdm-provision.py`, Headwind MDM 7.04.1): most private REST success bodies are wrapped in `{status, message, data}`; module 67 unwraps them via `_envelope_data` while still tolerating bare lists. The only flat (non-envelope) endpoints are `POST /rest/public/jwt/login` and `GET /rest/public/qr/json/{key}`. Error bodies may come back as HTTP 200 with `status: ERROR` and an i18n `message` key, and private auth failures can surface as Tomcat HTML 401/403 — an ERROR envelope raises `Headwind API error: <message>` with the i18n message bounded to 200 chars, a missing message or other unexpected shape falls back to a bounded body-shape note (key names, list length), and Tomcat HTML is reduced to a one-line title/text.

Superseded modules are archived under `plans/archive/system/`, never
left beside their replacements. The old `install.sh` orchestrator is
archived there too; it never runs again.
