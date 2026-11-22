# SKILL.md format rules

Reference only. Read when creating a skill, or before committing a change to one.

## Frontmatter
- Portable minimum: `name` and `description`. Other fields are ignored or hard-error elsewhere; leave them out.
- `name`: 1–64 chars, `a-z0-9-` only, no leading/trailing/consecutive hyphen, must equal the directory name. Never `anthropic` or `claude`.
- `description`: non-empty, ≤1024 chars, no XML tags, no first person.
- Optional: `metadata: {opencode/autoinvoke: false}` or `disable-model-invocation: true` hides a skill from the model's list while keeping it loadable by ID.

## Where skills live here
- Source: `dev/harness/skills/<name>/SKILL.md`.
- Deploy: `make dev` copies to `~/.agents/skills/`, read by OpenCode, Pi and Codex.
- OpenCode also scans `~/.config/opencode/skills`, `~/.claude/skills`, and project `.opencode/skills` / `.claude/skills` / `.agents/skills`.
- The directory name is the ID. OpenCode treats frontmatter `name` as a display label only.

## What the model sees
- Every model step: ID, name, description. Never the body, never reference files.
- On trigger: the body, plus the skill directory and up to ten supporting file paths.
- Consequence: the description is the only thing that makes a skill fire; the body is the only thing that acts.

## Limits
- House: body ≤30 lines, whole skill ≤120 lines. These override the upstream numbers below.
- Anthropic: body <500 lines / <5k tokens; references one level deep; a TOC for reference files over 100 lines (skill-creator says 300). Use 50 here.
- No validator exists in this repo and OpenCode V2 does not enforce these rules. You are the check.

## Sources
- https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
- https://agentskills.io/specification
- https://opencode.ai/docs/skills
- Anthropic's `skill-creator` (485 lines, ~75% eval/Claude-product specific) is a reference, not a template. Do not vendor it.
