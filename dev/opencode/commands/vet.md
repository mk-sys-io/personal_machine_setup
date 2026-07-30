---
description: Vet a third-party OSS dependency before suggesting it
agent: build
---

Vet dependencies passed as arguments using the dep-vet skill.

**Args:**
- Space-delimited by the CLI — each positional (`$1`, `$2`, …) is one dep
- Strip trailing `@<version>` (e.g. `express@4.18.2` → `express`) before lookup
- Match patterns: `http://`, `https://`, `github.com/`, `git@` → use as repo URL
- Otherwise treat as a package name and resolve from registry

**Per dep:**
Apply the skill workflow. If not found, report "not found — verify name".
