import type { Plugin } from "@opencode-ai/plugin"

// Golden rules — behavior instructions delivered to every model call.
// Three layers: (1) the full block at the top of the global AGENTS.md —
// the universal floor every agent, subagent, and mode inherits with zero
// configuration; (2) this plugin concats the ~50-token short form onto
// every wire call; (3) the compacting hook below preserves the full block
// across session summaries. String-literal payloads only: zero file reads
// (plugin-runtime I/O likely bypasses `permission` gates). Fail-open: any
// error leaves the system array / compaction context untouched. Pinned to
// binary 1.18.x + `@opencode-ai/plugin` 1.16.2 — experimental hooks may
// break without notice; if they do, delete this file rather than keeping
// a degraded half-working hook.

const SHORT_FORM = `Evidence first: quote file:line/output. Check premises for false assumptions; correct them. Never guess APIs/versions/paths. Missing→STOP, state gap. Be honest: flag flaws and uncertainty. No preamble/recap; bullets over prose; errors verbatim.`

const FULL_BLOCK = `## Golden rules
Evidence before synthesis: quote file:line or command output before concluding. Before answering, check the request for false assumptions or misconceptions; correct them. Never guess APIs, versions, paths, or owner/repo — resolve via docs, Context7, or registry spec first. Missing or ambiguous → STOP, state what's missing; never fill gaps. Be honest in reports: flag flaws, risks, and uncertainty. Verify by execution: read the file, run the command; error → fix now, warning → fix or note trade-off. Emit only needed tokens: no preamble/recap; bullets over prose. Keep errors/IDs/numbers verbatim. Expand only when uncertain, ambiguous, flaws found, or user asks why.`

const DEBUG = process.env.GOLDEN_RULES_DEBUG === "1"

async function log(
  client: unknown,
  level: "debug" | "info" | "warn",
  message: string,
  extra?: Record<string, string>,
) {
  try {
    await (client as { app: { log(o: unknown): Promise<void> } }).app.log({
      body: { service: "golden-rules", level, message, extra },
    })
  } catch {
    // logging must never break the plugin
  }
}

export const GoldenRulesPlugin: Plugin = async ({ client }) => {
  await log(client, "info", "golden-rules loaded")

  return {
    // Layer 2 — every-wire-call reinforcement. Concat onto system[0], never
    // push() — OpenAI-compat providers allow a single system message (#34243).
    "experimental.chat.system.transform": async (input, output) => {
      try {
        output.system[0] = (output.system[0] ?? "") + "\n\n" + SHORT_FORM
        if (DEBUG) {
          await log(client, "debug", "transform fired", {
            sessionID: input.sessionID ?? "none",
          })
        }
      } catch (error) {
        await log(client, "warn", "transform failed, system untouched", {
          sessionID: input.sessionID ?? "none",
          error: String(error),
        })
      }
    },
    // Layer 3 — compaction survival (documented example pattern).
    "experimental.session.compacting": async (input, output) => {
      try {
        output.context.push(FULL_BLOCK)
      } catch (error) {
        await log(client, "warn", "compacting push failed", {
          sessionID: input.sessionID,
          error: String(error),
        })
      }
    },
  }
}
