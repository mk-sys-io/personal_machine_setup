---
description: Preview a plan from plans/ — then /proceed to implement
agent: plan
---

$1 — plan name. If empty, acknowledge "No plan specified. Usage: /brief <name>" and stop.

Read plans/$1.md with the Read tool. If that file doesn't exist, use Glob to check for plans/$1/ directory and read plans/$1/$1.md or plans/$1/README.md. If still not found, acknowledge "Not found: $1" and stop — do not list available plans.

$ARGUMENTS

Present:
1. First 3 implementation steps (or first logical chunk if plan has sections)
2. For each step: target files to change, code patterns, commands to run
3. Your intended implementation approach

Keep it scannable (3-10 lines). Do not execute anything.

End with: "Review above. Type /proceed to implement."
