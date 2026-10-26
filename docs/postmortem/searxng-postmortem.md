# SearXNG — removal postmortem

Removed 2026-10-23. SearXNG no longer met the goal: structural customization
required forking/maintaining upstream code, and AI response synthesis was not
native nor customizable. Goal pivots to an answer engine with native,
customizable AI responses.

## Recoverability

All deleted files below remain in git history and can be restored with:
`git restore --source=<commit> -- <path>` or `git checkout <commit> -- <path>`.

## Deleted files (repo)

- `lib/25-searxng.py` — installer (clone + venv + systemd unit + CSS injection)
- `services/search/searxng-settings.yml`
- `services/search/searxng.service`
- `services/search/searxng-modern-dark.css`
- `services/search/limiter.toml`
- `services/` (directory, now empty)
- `docs/searxng.md`

## Deleted references (edited)

- `install.py`: `StepDef("25-searxng", ...)` block + `"25": "25-searxng"` alias
- `tools/verify.py`: `check_searxng()` + `("searxng", check_searxng)` entry
- `dotfiles/browsers/chrome/policy.json.template`: `DefaultSearchProvider*` keys (reverted to browser default)
- `lib/README.md`: `25-searxng.py` row
- `lib/helpers/README.md`: `25-searxng.py` consumer
- `docs/adr/011-refuse-root-precondition.md`: `25-searxng.py` entry
- `etc/restic/excludes.txt`: searxng exclude lines
- `lib/helpers/preconditions.py`: precedent comment (historical note retained)

## Live artifacts (NOT removed — repo-only change)

These remain on the workstation until manually cleaned:
`~/searxng` clone, `~/.config/systemd/user/searxng.service`, enabled/active
`searxng.service` unit. Cleanup: `systemctl --user disable --now searxng`,
remove the unit + clone, `systemctl --user daemon-reload`.

## Considered alternatives

| Engine | Verdict | Notes |
|---|---|---|
| Perplexica / Vane (MIT) | Top pick | Native cited AI answers; `systemInstructions` config knob (no fork); can reuse existing SearXNG via slim image. Docker-based. |
| Khoj (AGPL-3.0) | Deepest AI customization | Custom agents with persona/knowledge/tools via admin UI; heavier (8GB RAM, embeddings); "second brain", not pure search. |
| Morphic (Apache-2.0) | Best UI, fails no-fork | Generative UI; AI prompts hardcoded in source → requires fork. |
| OpenPerplex (MIT) | Not recommended | Stale, sparse config, hosted Metorial for prompts. |
| MindSearch (Apache-2.0) | Not recommended | Stale (v0.1.0, Nov 2024), Chinese-optimized models, prompts in code. |
| searxng-ai fork | Cheapest, keeps setup | Quick Summary via OpenAI-compatible API; inherits fork-lag risk; loses answer-engine UX. |

## Open questions

- AI provider: local (Ollama) vs cloud API.
- UX: educational/beneficial answers vs spoon-feeding.
- Deployment model: Docker vs systemd/venv.
- Structural customization must not require forking upstream.