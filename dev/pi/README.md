# Pi

Pi-specific config: `deploy/` mirrors `~/.pi/agent` (Pi's global agent dir).
Shared harness assets live in `dev/harness/` and are deployed to every agent's
expected path by `make dev`.

`deploy/extensions/scout-models.ts` registers the `scout` provider from
`~/.local/state/scout/catalog.display.json` and watches that file so Pi's model
picker follows scout's automatic updates. There is no committed `models.json`;
scout remains the only owner of the catalog.

Display names are composed as `<modelId> [badge] [ctx]` (e.g.
`lfm-3-1.7b:free [suffix] [262.1K]`). The context-window suffix is only added
when scout reports a real value (`contextWindow` non-null); otherwise it is
omitted, while the numeric `contextWindow` field still falls back to 128k. The
`[ctx]` formatting matches Pi's own token-count formatting (`256K`, `1M`).

Verify: `tsc --noEmit -p dev/pi`.
