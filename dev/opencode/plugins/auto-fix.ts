export default async ({ $ }) => {
  return {
    "tool.execute.after": async (input, output) => {
      if (input.tool !== "edit" && input.tool !== "write") return
      const file = output.args?.filePath ?? output.filePath ?? input.args?.filePath
      if (typeof file !== "string" || !file.endsWith(".py")) return
      await $(`ruff check --fix ${file}`).catch(() => {})
    },
  }
}
