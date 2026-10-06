---
description: Vet a third-party OSS dependency before suggesting it
agent: build
---

Vet dependencies passed as arguments using the `dep_vet` tool.

**Args:**
- Space-delimited by the CLI — each positional (`$1`, `$2`, …) is one dep
- Strip trailing `@<version>` (e.g. `express@4.18.2` → `express`) before lookup
- Match patterns: `http://`, `https://`, `github.com/`, `git@` → use as repo URL
- Otherwise treat as a package name and resolve from registry

**Per dep:**
Call the `dep_vet` tool with the dep spec, then run the `dep-vet` skill workflow (Repo Resolution, decision tree, verdict).
