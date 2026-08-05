---
description: Execute a plan — from a direct plans/ file or live chat
agent: build
---

$ARGUMENTS

$1 — optional plan name. If provided, read plans/$1.md. If the file exists, execute it. If not, acknowledge "Not found: plans/$1.md" and stop.

If no argument was given, check conversation history in this order:
1. Agreed implementation steps from live chat — implement them
2. Neither exists — state "Nothing to proceed on" and stop

Execute one step at a time with todowrite tracking:
- Mark each item in_progress before starting
- Mark completed after finishing and verifying
- If a step fails, stop and report — do not proceed

Scope: only touch files the plan mentions. If a step is ambiguous, ask — do not guess.

After all steps, present the completed todo list — every file modified, created, or deleted.
