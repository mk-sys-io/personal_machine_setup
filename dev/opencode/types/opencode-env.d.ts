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

interface ImportMeta {
  dir: string
}
