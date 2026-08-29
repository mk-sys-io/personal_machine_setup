# LibreWolf / Firefox Retrospective

Tried LibreWolf, then Firefox, as a daily driver. Installation worked; every
failure was config-level.

## Profile mismatches at the config level

Deployed `user.js`/`chrome/` landed in a profile the browser wasn't running —
LibreWolf's `profiles.ini` `[Install]` mismatch, and Firefox's
dedicated-profiles-per-install (FF 67+) orphaning configs. Tried
`MOZ_LEGACY_PROFILES=1` + a single `Default=1` profile.

## Inconsistent dark theme

RFP-on-default forces `prefers-color-scheme: light`; fork divergence breaks
the userChrome port ecosystem. Tried FPP `-CSSPrefersColorScheme` override +
`ui.systemUsesDarkTheme=1`.

## SearXNG not applied — not even an option

`custom.json` used the invalid `Prepend` key instead of `Add`, so SearXNG was
never registered and `Default` silently fell back to Google; `keyword.URL` is
dead since FF 23. Tried the `SearchEngines` policy (`Add`/`Default`/`Remove`/
`PreventInstalls` + `ExtensionSettings` blocking).

## Outcome

All fixes implemented, **none worked**. Two days of debugging and research
that changed nothing — a time sink. Dropped it. Stock Firefox + Betterfox
remains the canonical target; the browser stack is parked.