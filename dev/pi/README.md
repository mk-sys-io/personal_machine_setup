# Pi

Pi-specific config: `deploy/` mirrors `~/.pi/agent` (Pi's global agent dir).
Shared harness assets live in `dev/harness/` and are deployed to every agent's
expected path by `make dev`.

`deploy/extensions/scout-models.ts` registers the `scout` provider from
`~/.local/state/scout/catalog.display.json` and watches that file so Pi's model
picker follows scout's automatic updates. There is no committed `models.json`;
scout remains the only owner of the catalog.

Verify: `tsc --noEmit -p dev/pi`.
