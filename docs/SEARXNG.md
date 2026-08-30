# SearXNG

Privacy metasearch engine, served locally at `http://127.0.0.1:8888` and set as
the locked default search in Chrome/Brave (see `dotfiles/browsers/*/policy.json.template`).

## Setup

- Runs from `~/searxng` (git clone) in a Python venv, under a **systemd user**
  unit (`searxng.service`). No sudo, no container.
- `services/search/` is the source of truth. `lib/25-searxng.sh` deploys it:
  copies `searxng-settings.yml` + `searxng.service`, injects the theme CSS, and
  enables/starts the unit. Re-run after any edit there.

## Settings (`searxng-settings.yml`)

- `use_default_settings.engines.keep_only` whitelists engines — **no image or
  video engines**, so no images/videos in results.
- `categories_as_tabs: general` only — no images/videos/science tabs.
- `ui.theme_args.simple_style: dark` forces dark; `preferences.lock` locks
  `theme` + `simple_style` so it can't be changed.

## Theme

The layout is the "modern" theme from `kilianramharter/searxng-modern-theme`,
compiled to a **dark-only** CSS override (`services/search/searxng-modern-dark.css`).
`25-searxng.sh` appends it to the stock `simple` theme CSS (marker-delimited,
idempotent). No templates are changed — it's pure CSS over the stock markup.

## Maintenance risk

The modern theme is pinned to SearXNG `2026.7.25`; installed is `2026.8.28`.
The CSS targets stable classes, but a future SearXNG markup change could break
the layout. On SearXNG upgrades, re-verify the layout and re-sync the CSS from
the upstream theme repo if needed.
