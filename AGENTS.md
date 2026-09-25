---
title: "AI Agents Guide: siat-verification"
description: Intent routing table, operational commands, and agent rules index for siat-verification repository
status: active
category: index
tags: [ai-first, agent, automation, fastapi, hermes, neo4j, sdmx, siat, verification, fact-check]
last-verified: 2026-09-10
summary-ru: Таблица маршрутизации намерений и индекс правил для AI-агентов
summary-uz: AI agentlar uchun maqsadlar marshrutlash jadvali va qoidalar indeksi
related:
  - knowledge/synthesis/context-brief.md
  - knowledge/synthesis/decisions-log.md
  - knowledge/database/neo4j-graph-guide.md
  - knowledge/methodology/siat-verification-methodology.md
  - .agents/rules/source-trust-levels.md
  - .agents/rules/doc-standards.md
  - .agents/rules/ai-first.md
  - .agents/rules/karpathy-guidelines.md
---

# AGENTS.md — AI Agent Routing Guide

> **First file to read**: `knowledge/synthesis/context-brief.md`
> **Access rules**: `.agents/rules/source-trust-levels.md`

---

## Intent Routing Table

| Intent | Location | File / Entry Point |
|---|---|---|
| Understand project context & cold-start | knowledge/synthesis/ | [context-brief.md](knowledge/synthesis/context-brief.md) |
| Find architectural decisions (ADRs) | knowledge/synthesis/ | [decisions-log.md](knowledge/synthesis/decisions-log.md) |
| Neo4j graph schema & Cypher query guide | knowledge/database/ | [neo4j-graph-guide.md](knowledge/database/neo4j-graph-guide.md) |
| SIAT fact-checking & tolerance methodology | knowledge/methodology/ | [siat-verification-methodology.md](knowledge/methodology/siat-verification-methodology.md) |
| Press Office & AI governance regulations | knowledge/regulations/ | [README.md](knowledge/regulations/README.md) |
| Central AI governance regulations (Order 56) | gis-ai-dept | `knowledge/regulations/` in `gis-ai-dept` |
| Fast-API application entry point & routes | app/ | [api.py](app/api.py), [main.py](app/main.py) |
| Hermes Host Agent lifecycle & runner | agents/ | [hermes_host.py](agents/hermes_host.py) |
| Per-user profile isolation (`X-User-Id` → Hermes home) | agents/ | [user_profiles.py](agents/user_profiles.py) |
| Per-user rate limiting | app/ | [rate_limit.py](app/rate_limit.py) |
| Background turns & job ownership | app/ | [jobs.py](app/jobs.py) |
| Press-release indicator lookup (`statind_code`) | plugins/pressreliz/ | [statind.py](plugins/pressreliz/statind.py) |
| Published observations query (`statind_data`) | plugins/pressreliz/ | [data.py](plugins/pressreliz/data.py) |
| SDMX source URL resolution (`statind_data_url`) | plugins/pressreliz/ | [sdmx.py](plugins/pressreliz/sdmx.py) |
| Pressreliz plugin registration & toolset | plugins/pressreliz/ | [__init__.py](plugins/pressreliz/__init__.py) |
| PDF to Markdown conversion (`pdf_to_md`) | plugins/pdfmd/ | [convert.py](plugins/pdfmd/convert.py) |
| PDF claim extraction (`pdf_extract`) | plugins/pdfmd/ | [extract.py](plugins/pdfmd/extract.py) |
| Telegram post formatting & verification gate | plugins/telegram/ | [post.py](plugins/telegram/post.py) |
| Agent coordinator prompt & system instructions | prompts/ | [hermes_coordinator.md](prompts/hermes_coordinator.md) |
| Host agent soul & identity definition | prompts/ | [soul.md](prompts/soul.md) |
| Restore Neo4j dump into container | scripts/ | [restore_dump.ps1](scripts/restore_dump.ps1) |
| Container startup & plugin sync script | scripts/ | [start.sh](scripts/start.sh) |
| Service healthcheck script | scripts/ | [healthcheck.sh](scripts/healthcheck.sh) |
| Docker Compose multi-service definition | root | [docker-compose.yml](docker-compose.yml) |
| Environment variables template | root | [.env.example](.env.example) |

---

## Canonical Single Verification Command

Agents must run this single command to verify test suite health before concluding tasks:

```bash
python -m pytest tests/ -v --tb=short
```

---

## Default Rules

1. **Cold start**: ALWAYS read `knowledge/synthesis/context-brief.md` first.
2. **Source trust**: L1 (`source/`) > L2 (`source/meetings/`) > L3 (`knowledge/`).
3. **Citations**: When creating L3 content, cite source in frontmatter (`derived-from:`).
4. **Meetings**: Read `source/meetings/_index.yaml` before opening individual meeting files.
5. **Do not touch**: `source/` — read-only, `source/meetings/*.md[final]` — read-only.
6. **Zero-PII & Zero-Hallucination**: Never output unverified numbers as verified (`tasdiqlangan`). Every code emitted by `statind_code` must originate from the Neo4j register.
7. **Language**: Technical documentation bodies in English. Human summaries via `summary-ru` / `summary-uz` frontmatter fields. Agent replies to users default to Uzbek.

---

## Installed Rules (`.agents/rules/`)

| File | Description |
|---|---|
| [ai-first.md](.agents/rules/ai-first.md) | AI-first development and maintenance principles |
| [doc-standards.md](.agents/rules/doc-standards.md) | Documentation standards (frontmatter, tags, naming, language) |
| [source-trust-levels.md](.agents/rules/source-trust-levels.md) | L1/L2/L3 access rules and workflows |
| [karpathy-guidelines.md](.agents/rules/karpathy-guidelines.md) | Behavioral guidelines to reduce LLM coding mistakes |
