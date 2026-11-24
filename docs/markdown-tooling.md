# Markdown linting (machine-wide)

Canonical source of truth for Markdown validation. Prettier still owns
formatting (Zed saves); markdownlint only gates structure. If this doc and
the config disagree, fix the doc in the same commit.

## Policy source

- `dev/markdownlint/.markdownlint.jsonc` — rules-only config. It sets
  `default: false` and enumerates every active rule, so a markdownlint
  upgrade cannot silently enable new rules.
- `make dev` deploys it to `~/.config/markdownlint/.markdownlint.jsonc`.
- No per-repo config is shipped. markdownlint-cli2 does not auto-discover
  `~/.config`; the hook and documented commands pass `--config` explicitly.

## Pre-commit hook

- `dev/git/pre-commit` is deployed to `~/.git-hooks/pre-commit` (global).
- It lints only staged `.md` files with a pinned
  `markdownlint-cli2@0.24.2`, `--no-globs`, and the shared config.
- Errors block the commit; warnings are printed but exit 0.
- It fails open only when `npx` or the shared config is absent. A lint
  error always blocks.
- `plans/` is gitignored, so it never reaches the hook.

## Severity policy

- **Error (blocks)** — structural breakage: heading levels and duplicate
  sections, blank lines around headings/lists/fences/tables, table column
  count, empty or broken in-page links, reference definitions, and
  reversed link syntax.
- **Warning (reports only)** — style and cosmetics: heading/list/emphasis
  style, trailing whitespace and tabs, multiple blank lines, bare URLs,
  fence language, final newline, code-span spacing, table pipe style.
- **Disabled** — line length, inline HTML, first-line heading, horizontal
  rule style, proper-name casing, link/image style, descriptive link
  text, and table column padding. See the config for the exact list.

## Commands

- `make md-lint` — lints tracked `.md` files with the checkout config.
- Any repo:
  `npx --yes markdownlint-cli2@0.24.2 --no-globs --config "$HOME/.config/markdownlint/.markdownlint.jsonc" -- <paths>`
- `--fix` is available for manual use; do not add it to the hook.

## Overrides

`--config` is a base: an explicit repo-local rule key overrides it, while
a repo-local `default` does not override explicit keys. Add a repo-local
config only for a deliberate exception; none is shipped by default.

## Limits

- MD051 validates in-page anchors only; cross-file links are not checked.
- MD052 checks full and collapsed reference links, not shortcut
  references.
- Unclosed or mismatched code fences are not detected.
- Formatting remains Prettier/Biome-owned; this gate is structural only.

## Bumping the pin

Change the version in `dev/git/pre-commit`, the `md-lint` recipe in
`Makefile`, and the manual command in `dev/harness/skills/docs-writing/SKILL.md`,
then re-run `make md-lint` and the audit.
