import { tool } from "@opencode-ai/plugin"
import path from "path"

// This tool runs inside opencode's embedded Bun runtime — Bun is NOT
// installed on this machine and is NOT required. opencode provides the Bun
// globals (`Bun.$`, `import.meta.dir`) at runtime. The tool cannot be
// executed directly via `node` or `bun` from the terminal.

export default tool({
  description: "Vet an OSS dependency across 6 metrics (activity, security, code quality, maturity, community, vibe-code). Returns structured JSON with per-metric availability flags.",
  args: {
    input: tool.schema
      .string()
      .describe(
        "Dependency spec: owner/repo, GitHub/GitLab URL, release-asset URL, or registry spec (pypi:name, npm:name, go:module, cargo:name, rubygems:name)",
      ),
    version: tool.schema
      .string()
      .optional()
      .describe("Optional pinned version for OSV vulnerability lookup"),
  },
  async execute(args) {
    const script = path.join(import.meta.dir, "dep_vet.py")
    const versionArg = args.version ? ` ${args.version}` : ""
    const result = await Bun.$`python3 ${script} ${args.input}${versionArg}`.text()
    return result.trim()
  },
})
