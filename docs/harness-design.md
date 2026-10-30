# Harness Design — Agent Setup Architecture

## Summary

First step: build Consult, Plan, and Build modes in Pi. Everything else
(personas, prompt co-authoring, decision models) attaches to these modes.

Key points:
- Consult mode = read-only, question-focused, surfaces unknowns; output is
  a context document, not a plan.
- Plan mode = read-only, produces an executable step-by-step implementation plan.
- Build mode = full tool access, executes the plan.
- Pi has no built-in plan mode; modes are behavioral overlays delivered as
  extensions.
- Decision models (Jev/Laya/etc.) steer context, surface skills/MCPs, and
  gate tool calls; they never generate text.

---

## 1. Consult vs. Plan vs. Build Modes

### 1.1 What each mode does

| Mode | Read-only? | Output | Tool access |
|------|-----------|--------|-------------|
| Consult | Yes | Context document (shared understanding) | read, grep, find, ls, bash (allowlisted) |
| Plan | Yes | Executable implementation plan | read, grep, find, ls, bash (allowlisted) |
| Build | No | Code changes, commits | All tools |

### 1.2 Evidence for the tripartite split

- Lovable, Cursor, Devin, Unity, and OpenCode all converged on Ask/Consult → Plan → Build/Agent.
- Pi's four runtime modes (Interactive, Print/JSON, RPC, SDK) are transport modes, not behavioral modes.
- The distinction is well-established; the implementation in Pi is the gap.

### 1.3 Pi implementation approach

- Modes are behavioral overlays delivered as TypeScript extensions, not core changes.
- Consult mode: injects a system prompt nudge + disables edit/write tools.
- Plan mode: tool gating + plan artifact extraction + progress tracking.
- Build mode: full tool access (Pi's default).

---

## 2. Consult Mode (the philosophy)

### 2.1 What consult mode produces

A context document — shared understanding, surfaced unknowns, identified
limitations, blind spots, and a recommended next step. Not an execution plan.

### 2.2 Consult vs. traditional plan mode — evidence

- "Guardrails Beat Guidance" (arXiv 2604.11088): every beneficial rule was
  a negative constraint ("do not edit yet"); every harmful one was a positive
  directive. Consult mode is fundamentally a negative constraint.
- "From Plan to Action" (arXiv 2604.12147): adding plan phases can degrade
  performance when they don't align with the model's internal strategy.
- VentureBeat study: more elaborate harnesses can triple cost without
  improving accuracy.
- Microsoft Research (VL/HCC 2026, N=24): task outcomes were identical
  between plan mode and no-plan mode; only process metrics differed.
- aihero.dev author switched from plan mode to "grilling" (consult mode).

### 2.3 When consult is better vs. when plan is better

- Consult: simple/well-understood tasks, low overhead, conversational,
  surfacing unknowns before committing.
- Plan: complex/ambiguous/high-stakes tasks, needs reviewable artifact,
  premature editing expensive to undo.

---

## 3. Persona Problem in Consult Mode

### 3.1 Why identity personas fail

- "You are a senior architect" produces no statistically significant
  improvement over unprompted baselines (ASDLC.io deprecation analysis).
- Persona prompting produces only small aggregate differences
  (arXiv 2605.29420, 1,140 questions, 38 roles, 6 domains).
- Expert personas under hierarchical coordination converge on "safe,
  high-level generalities" (ACL 2026 diversity collapse study).

### 3.2 Functional personas: trigger/goal/rules/boundaries

Each persona is a rule bundle, not a character description:

- **Trigger:** when it activates (e.g., "when task is building an app")
- **Goal:** what it helps the user reach a conclusion about
- **Rules:** specific behavioral constraints (what to ask, flag, assume)
- **Boundaries:** what it does NOT do (no implementation steps, no code review)
- **Output contract:** what the context document looks like

### 3.3 Existing implementations

- **grill-for-unknowns** (nicobailon): surfaces unknowns, produces a
  "launch packet" (assumptions, open questions, verification, rollback risks).
- **consult-llm/panel** (raine): role-asymmetric advisory panel (architect,
  security, maintainability, test-strategist); agent synthesizes trade-offs.
- **AgentCrew** (mlguyYT): automatic persona routing with no manual role
  selection.
- **Claude Managed Agents**: persona as versioned config (model + system
  prompt + tools + MCP + skills).

---

## 4. Prompt Co-Authoring (consult-mode workflow)

### 4.1 Four stages

1. **Interview (multi-turn):** extract goal, audience, format, constraints,
   tone, success criteria. One question at a time with recommended answers.
2. **Stabilize:** write the framing to a shared artifact (launch packet).
   Get explicit confirmation before proceeding.
3. **Draft:** write the prompt from the stabilized framing (role, task,
   context, format, constraints, examples).
4. **Refine:** present prompt, user tests it, feeds observations back,
   agent iterates incrementally.

### 4.2 Existing implementations

- **Microsoft Prompt Coach:** interactive prompt guidance through iterative
  feedback (creation, refinement, analysis).
- **Prompty interview pattern:** structured interview template before any
  prompt writing; dialogue runs until user signals completion.
- **Anthropic doc-coauthoring skill:** context gathering → refinement/
  structure → reader testing (three-phase flow).
- **Self-rewriting meta-prompt loop:** dual-agent write/critique with explicit
  quality gate as stopping condition.

---

## 5. Decision Models for Pi

### 5a. What Jev is and how it integrates

- Jev is a "System One" decision model — fast, cheap, non-generative.
  Returns typed decisions (Choice, Noul, Score, Rank, Ask) with calibrated
  confidence. ~$0.042/M input, output free, ~0.4s latency.
- Pi integration: `pi-jev` (tool routing, compaction, auto-model, agent
  orchestration), `jev-code` (native System One tools), `jev-skill-router`
  (O(1) skill/MCP selection), `pi-mcp-adapter` (semantic tool search).
- Jev never writes text or code. It steers; the main model generates.

### 5b. Pricing and free alternatives

- Jev: no free tier (waitlist). $0.042/M input, output free.
- Vercel AI Gateway: $5/mo free credit covers ~119M input tokens.
- Free local: Laya (Apache-2.0, 421M), LiquidAI/d1-omni-600M,
  Mapika/decider-2b (Apache-2.0), Cloudflare Clef (open weights).
- `pi-jev-auto-mode` supports swapping providers (Jev, Clef, Strands
  Decider, or any OpenAI-compatible endpoint).

### 5c. Hardware assessment

- GPU: RTX 3050 Laptop, 6 GB VRAM, compute cap 8.6, passive cooling.
- RAM: 15 GB total, 9.2 GB available.
- CPU: i5-13450HX (8 cores, 16 threads).
- Disk: 194 GB available.
- No inference software installed (no Ollama, PyTorch, transformers).
- Can run: d1-omni-600M (1.5 GB), Von (1 GB), decider-2b int4 (1.5 GB).

### 5d. Context window vs. VRAM vs. heat trade-offs

- VRAM has two components: model weights (fixed) + KV cache (grows linearly
  with context length).
- Context window is the hidden VRAM killer: 8B model at 8K context can use
  4 GB of KV cache alone.
- Thermal: RTX 3050 laptop has no dedicated GPU fan; sustained 80W+ draw
  pushes chassis to 85-95°C with max fan noise.
- Sweet spot: 3-4B models at Q4 with 4K-8K context (~3-4 GB VRAM, ~20-25
  tok/s, moderate heat). Cap GPU at 60W for quieter operation.
- Decision models use 1-2 GB VRAM, 50+ tok/s, negligible heat — run 24/7.

### 5e. Free-tier decision models with large context

| Approach | Context window | Speed | Reliability | Cost |
|----------|---------------|-------|-------------|------|
| System One (Laya, Von, decider-*) | 512-8K | Very fast | High (typed) | Free (local) |
| Free LLM API (Gemini, NVIDIA NIM) | 131K-1M | Slower | Lower (prompt-dependent) | Free (rate-limited) |
| Jev (TypeSafe) | 64K | Very fast | High | $0.042/M |

- Gemini free tier: 1M context, 10 RPM, 1,500 RPD.
- NVIDIA NIM free tier: 131K-262K context, 40 RPM.
- Hybrid: Laya for fast routing/guardrails, Gemini for large-context analysis.

### 5f. Decision model input context design

- Send: current user prompt (full) + last assistant message (whole) +
  earlier turns (opening lines, newest first) = ~8,000 chars (~2K tokens).
- Do NOT send: tool output, file contents, CLAUDE.md, full history.
- Why not full context: noise degrades accuracy, exceeds decision model's
  window, costs 40x more per turn.
- Why not just new prompt: misses "go" intent — decision model needs the
  last assistant message (which contains the plan) to route correctly.
- 8K bounded slice is the industry-converged sweet spot (jev-skill-router,
  TypeSafe Router, OpenRouter Jev Router).