// Typecheck-only ambient declarations for opencode custom tools.
// Runtime module (@opencode-ai/plugin, Bun, import.meta) comes from opencode's
// embedded Bun runtime — these declarations exist only so `tsc -p` passes.
// This file is NOT deployed (excluded in `make dev`).

declare module "@opencode-ai/plugin" {
  export type ToolExecuteArgs = { input: string; version?: string }

  export const tool: {
    schema: {
      string(): {
        describe(desc: string): unknown
        optional(): { describe(desc: string): unknown }
      }
    }
    (definition: {
      description: string
      args: unknown
      execute(args: ToolExecuteArgs): string | Promise<string>
    }): unknown
  }

  // Minimal client + hook surface used by plugins/*.ts. Shapes mirror the
  // installed `@opencode-ai/plugin` dist/index.d.ts (binary 1.18.x /
  // SDK 1.16.2) — re-check on upgrade; experimental hooks may change
  // without notice.
  export type Plugin = (
    ctx: {
      client: {
        app: {
          log(o: unknown): Promise<void>
        }
      }
    },
  ) => Promise<{
    "experimental.chat.system.transform"?: (
      input: { sessionID?: string; model: unknown },
      output: { system: string[] },
    ) => Promise<void>
    "experimental.session.compacting"?: (
      input: { sessionID: string },
      output: { context: string[]; prompt?: string },
    ) => Promise<void>
  }>
}

declare module "path" {
  export function join(...parts: string[]): string
  const _default: { join(...parts: string[]): string }
  export default _default
}

declare const Bun: {
  $: (
    strings: TemplateStringsArray,
    ...expr: unknown[]
  ) => { text(): Promise<string> }
}

// Bun provides `process.env` at runtime — declared here so `tsc -p` passes.
declare const process: {
  env: Record<string, string | undefined>
}

interface ImportMeta {
  dir: string
}
