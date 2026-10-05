# Golden rules — universal behavior delivery (P8)

Static behavior text must reach EVERY model call regardless of agent,
subagent, or mode. `agent.<name>.prompt` cannot do this (per-agent layer by
construction; subagents run fresh child sessions with their own prompts).
Three layers, no repetition:

**Layer 1 — global `AGENTS.md` top (universal floor).** Full block below,
pinned above all other sections. Instruction sources combine (agent prompt,
env/date, AGENTS.md discovery, skills/MCP) for every agent on every call —
new agents inherit it with zero configuration. Stable across v1→v2.

**Layer 2 — `plugins/golden-rules.ts` (every-wire-call reinforcement).**
`experimental.chat.system.transform` concats the short form onto
`output.system[0]` (concat, never push — OpenAI-compat allows one system
message, #34243). Fail-open try/catch. Pinned to binary 1.18.x +
`@opencode-ai/plugin` 1.16.2; experimental may break without notice —
remove-don't-degrade, never keep degraded (researcher-subagent precedent).
`shell.env` and `tui.prompt.append` are NOT model injectors (env vars and
TUI input box respectively). Global `instructions[]` is dead in v2 — do
not use.

**Layer 3 — `experimental.session.compacting` (compaction survival).**
Pushes the full block into compaction context per the documented example.
Pointer+confirm reserved as post-compaction recovery only.

## Full block (verbatim everywhere, never paraphrased)

## Golden rules
Evidence before synthesis: quote file:line or command output before concluding. Before answering, check the request for false assumptions or misconceptions; correct them. Never guess APIs, versions, paths, or owner/repo — resolve via docs, Context7, or registry spec first. Missing or ambiguous → STOP, state what's missing; never fill gaps. Be honest in reports: flag flaws, risks, and uncertainty. Verify by execution: read the file, run the command; error → fix now, warning → fix or note trade-off. Emit only needed tokens: no preamble/recap; bullets over prose. Keep errors/IDs/numbers verbatim. Expand only when uncertain, ambiguous, flaws found, or user asks why.

## Short form (~50-token always-on essentials, every-wire-call payload)

Evidence first: quote file:line/output. Check premises for false assumptions; correct them. Never guess APIs/versions/paths. Missing→STOP, state gap. Be honest: flag flaws and uncertainty. No preamble/recap; bullets over prose; errors verbatim.

Short-form selection: evidence-first, falsification, no-guess, STOP, and
honesty are judgment rules (needed before every answer, decay fastest);
delivery rules ride along because every turn emits output. Tool routing
(Context7 resolve→query, registry spec) and verify tiers stay in the full
block only — reference detail, stable once loaded.

## Why this wording (research, not taste)

Falsification procedure ("check for false assumptions, correct them")
nearly quadrupled premise-catching 0.16→0.60 (Accommodation 2026);
honesty instruction took flaw-flagging 2/200→190/200 in coding-agent
reports (Insecure Reporters 2026); rephrase-as-question beats explicit
"don't be sycophantic" (Ask-Don't-Tell 2026). Bare "be skeptical" /
"don't be sycophantic" slogans fail or backfire (ELEPHANT 2025) — every
line here is a procedure, not a moral. Models detect ambiguity but don't
act on it ("knowing but not showing", Su & Cardie 2026) — hence STOP is
a command. CoT/deep-reasoning does not fix framing sycophancy — grounding
does. Verbosity line follows the Claude Concise pattern: positive delivery
+ preserve-list + expand-triggers + conflict-wins; never persona (caveman
fragments/abbreviations are where semantic damage lives), never pure
negation ("don't be verbose" compresses what matters too).
