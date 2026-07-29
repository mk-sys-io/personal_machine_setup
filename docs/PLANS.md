# `plans/` — Personal scratch folder

A `plans/` directory at any repo root is **globally ignored by git** on this
machine (via `~/.config/git/ignore`). Use it for drafting AI agent plans,
to-do lists, local notes, and any scratch files that belong in the working
tree but must **never** be committed or pushed.

## Workflow

1. Run `init` in any repo root — creates `plans/` and `.ignore`
2. Draft files in `plans/` with `@` references in opencode
3. Copy files to their real destination when ready

## `@` references in opencode

opencode uses ripgrep for file discovery, which respects gitignore rules.
Since `plans/` is globally git-ignored (`~/.config/git/ignore`), it's invisible
to `@` by default.

The `.ignore` file tells ripgrep to include `plans/` anyway
(`!plans/` un-ignores it). `.ignore` is committed to git — it's a standard
ripgrep config file, not a gitignore, so it doesn't affect version control.

## Edge cases

- **Files already tracked by git** — the global ignore only affects *untracked*
  files. If a file inside `plans/` is accidentally `git add`-ed, it will be
  tracked going forward regardless of the ignore rule.
- **Existing `plans/` in a project** — if a project already has a committed
  `plans/` directory (from the upstream, not from you), it will still appear in
  `git status` normally. The ignore only prevents new untracked content from
  showing up.
- **Submodules** — global `~/.config/git/ignore` applies to submodules too.
- **No remote backup** — files in `plans/` are local-only. They are not backed
  up by `git push`.
- **Any repo on this machine** — the rule is global. If you start a project
  where `plans/` has a legitimate meaning you want tracked, remove it from
  the global ignore and use a `.gitignore` override or `.git/info/exclude`.
