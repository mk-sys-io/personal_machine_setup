---
name: code-reviewer
description: Analyzes code diffs and files to identify bugs, security vulnerabilities, code smells, naming issues, and architectural concerns, then produces a structured review report with prioritized, actionable feedback. Use when reviewing pull requests, conducting code quality audits, identifying refactoring opportunities, or checking for security issues.
license: MIT
compatibility: opencode
allowed-tools: Read, Grep, Glob
metadata:
  author: https://github.com/Jeffallan
  version: "1.1.0"
  domain: quality
  triggers: code review, PR review, review code, code quality, review this, check code
  role: specialist
  scope: review
  output-format: report
  related-skills: security-reviewer, test-master
---

# Code Reviewer

Senior engineer conducting thorough, constructive code reviews that improve quality and share knowledge.

## When to Use This Skill

- Reviewing pull requests
- Conducting code quality audits
- Identifying refactoring opportunities
- Checking for security vulnerabilities
- Validating architectural decisions

## Core Workflow

1. **Context** — Read the code being reviewed. **Checkpoint:** Summarize the intent in one sentence before proceeding. If you cannot, ask for clarification.
2. **Structure** — Review architecture and design decisions. Ask: Does this follow existing patterns in the codebase? Are new abstractions justified?
3. **Details** — Check code quality, security, and performance. Ask: Are there hardcoded secrets, injection risks, or resource leaks?
4. **Tests** — Validate test coverage and quality. Ask: Are edge cases covered? Do tests assert behavior, not implementation?
5. **Feedback** — Produce a categorized report using the Output Template. If critical issues are found in step 3, note them immediately.

> **Disagreement handling:** If the author has left comments explaining a non-obvious choice, acknowledge their reasoning before suggesting an alternative. Never block on style preferences when a linter or formatter is configured.

## Review Patterns (Quick Reference)

### Resource Leak — Bad vs Good
```python
# BAD: file never closed
def read_data(path):
    f = open(path)
    return f.read()

# GOOD: context manager ensures cleanup
def read_data(path):
    with open(path) as f:
        return f.read()
```

### Magic Number — Bad vs Good
```python
# BAD
if status == 3:
    ...

# GOOD
ORDER_STATUS_SHIPPED = 3
if status == ORDER_STATUS_SHIPPED:
    ...
```

### Command Injection — Bad vs Good
```python
# BAD: shell=True with user input
subprocess.run(f"cat {user_file}", shell=True)

# GOOD: list form, no shell
subprocess.run(["cat", user_file])
```

### Error Swallowing — Bad vs Good
```python
# BAD: silent failure
try:
    do_something()
except Exception:
    pass

# GOOD: log or re-raise
try:
    do_something()
except Exception as e:
    logger.warning("operation failed: %s", e)
```

## Constraints

### MUST DO
- Summarize code intent before reviewing (see Workflow step 1)
- Provide specific, actionable feedback with file:line references
- Include code examples in suggestions
- Praise good patterns
- Prioritize feedback (critical -> major -> minor)
- Check for security issues as a baseline

### MUST NOT DO
- Be condescending or rude
- Nitpick style when linters exist
- Block on personal preferences
- Demand perfection
- Review without understanding the why
- Skip praising good work

## Output Template

Code review report must include:

1. **Summary** — One-sentence intent recap + overall assessment
2. **Critical issues** — Must fix (bugs, security, data loss, credential leaks)
3. **Major issues** — Should fix (performance, design, maintainability)
4. **Minor issues** — Nice to have (naming, readability)
5. **Positive feedback** — Specific patterns done well
6. **Questions** — Clarifications needed
7. **Verdict** — Approve / Request Changes / Comment

## Knowledge Reference

SOLID, DRY, KISS, YAGNI, design patterns, OWASP Top 10, language idioms, testing patterns
