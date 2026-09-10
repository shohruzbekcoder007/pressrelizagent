# AI-First Documentation Standards

> **Purpose:** Mandatory directives for any AI agent (Antigravity, Cursor, Hermes) creating or modifying `.md` files in this repository. Violating these rules degrades the entire repository's AI-readability.

## 1. Frontmatter is Mandatory

Every new `.md` file **must** start with YAML frontmatter using the standard schema:

```yaml
---
title: English title (primary key for all routing)
description: One-line English purpose summary (for AI routing)
status: active | pending | done | future
category: runbook | architecture | model | phase | reference | security | status | rule | index
tags: [keyword1, keyword2, keyword3]
last-verified: YYYY-MM-DD
derived-from: source/path/to/original  # if applicable
summary-ru: Optional Russian summary for humans
summary-uz: Optional Uzbek summary for humans
related:
  - path/to/related-file.md
---
```

**Required fields:** `title`, `description`, `status`, `last-verified`, `category`
**Recommended fields:** `tags`, `derived-from`, `summary-ru`, `summary-uz`, `related`

## 2. English `title` and `description` Required

- **`title`**: Always in English. This is the primary key for AI routing, search, and indexing.
- **`description`**: Always in English (one line, no markdown, no punctuation at end).
- Localized human summaries go into `summary-ru` and `summary-uz`.

## 3. Controlled Vocabulary Tags

Tags must come from the established vocabulary:
`ai-first`, `agent`, `automation`, `api`, `fastapi`, `hermes`, `neo4j`, `graph`, `sdmx`, `siat`, `indicators`, `press-release`, `pdf`, `telegram`, `verification`, `fact-check`, `methodology`, `regulation`, `standards`

Every documentation file must have at least 2 tags.

## 4. Cross-Reference Rule

If your document body text references another file by relative path (e.g., `../database/neo4j-graph-guide.md`), that file **MUST** also appear in your `related:` frontmatter list.

## 5. Copy-Paste Commands

Every command in documentation must be:
- **Copy-pasteable** — no unexplained placeholders.
- **Host/Container-specific** — always specify whether it runs on host workstation, Docker container, or remote server.
- **Expected output** — describe the expected output or success indicator.

## 6. Update AGENTS.md

When creating a new guide, script, plugin, or tool, you **must** add an entry to the intent routing table in `AGENTS.md`.

## 7. No Orphan Files

Every new file must be referenced from at least one index:
- `README.md`
- `AGENTS.md` (intent routing table)
- `knowledge/synthesis/context-brief.md`

## 8. Language Convention

- **Document body:** English (AI-first processing language).
- **`summary-ru` / `summary-uz`:** Localized summaries for human readers.
- **Code blocks, commands, YAML:** English.
- **`source/` files:** Original language preserved (READ-ONLY).
