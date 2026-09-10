---
title: "AI Agent Cold-Start Brief: siat-verification"
description: Cold-start briefing for AI agents — project state, key decisions, trust levels, priorities
status: active
category: reference
tags: [ai-first, agent, project, automation, neo4j, fastapi, hermes, sdmx, siat, verification]
auto-generated: false
last-verified: 2026-09-10
summary-ru: Брифинг для AI-агентов — состояние проекта, решения, уровни доверия, приоритеты
summary-uz: AI agentlar uchun brifing — loyiha holati, qarorlar, ishonch darajalari, ustuvorliklar
related:
  - decisions-log.md
  - ../database/neo4j-graph-guide.md
  - ../methodology/siat-verification-methodology.md
  - ../../.agents/rules/source-trust-levels.md
---

# AI Cold-Start Brief: siat-verification

> **Read this file first** at the beginning of every new session.
> It provides full project context in 60 seconds without reading all files.

---

## What is This Project

`siat-verification` (`pressrelizagent`) is the automated fact-checking and publication verification service for the Statistics Agency under the President of the Republic of Uzbekistan. It validates statistical claims in draft press releases, analytical reports, and media notices against the official **SIAT (StatInd)** database.

### Core Capabilities:
1. **Host Agent Execution**: Production-grade FastAPI service (`app/api.py`, `app/main.py`) driving a **Hermes Host Agent** with multi-turn session persistence, tool calling, and fallback to LangGraph (`hermes_lite`).
2. **Knowledge Graph Fact-Checking**: Direct integration with Neo4j containing **2.2 Million** published `Observation` nodes across **3,326** statistical indicators.
3. **Zero-Hallucination Indicator Retrieval**: `statind_code` performs Lucene fulltext search over Uzbek Latin, Cyrillic, and Russian terms, returning verified leaf candidates rather than letting LLMs invent codes.
4. **Document Ingestion**: `pdf_to_md` and `pdf_extract` converts incoming release PDFs to Markdown, structured claims, territorial breakdown (`manzil`), figures (`raqam`), and units (`birlik`).
5. **Telegram Gatekeeper**: `telegram_post` tool separates verified figures (`tasdiqlangan`) from unverified figures (`tasdiqlanmagan`) before any public dissemination.

---

## Source Trust Hierarchy

| Level | Directory | Access | Description |
|---|---|---|---|
| **L1** | `source/` | 🔒 READ-ONLY | Official classifiers, SDMX schema definitions, database dumps |
| **L2** | `source/meetings/` | ➕ APPEND-ONLY | Human meeting minutes, review logs (`final` = frozen) |
| **L3** | `knowledge/` | ✏️ READ+WRITE | AI-generated derivative guides, query recipes, methodology docs |

> **Conflict rule**: L1 > L2 > L3. On contradiction — trust the source with the lower level number.

---

## Key Decisions (Summary)

| ID | Decision | Date | Rationale |
|---|---|---|---|
| DEC-001 | Dual-backend runtime (`hermes` + `hermes_lite` fallback) | 2026-09-10 | Resilience against Hermes container bootstrapping issues |
| DEC-002 | Neo4j graph retrieval over prompt context stuffing | 2026-09-10 | Register is 146k tokens; retrieval takes <1s and a few hundred tokens |
| DEC-003 | Lucene fulltext search with level 4 leaf-node filtering | 2026-09-10 | Headings (levels 1–3) are not measurable and crowd out indicators |
| DEC-004 | Zero-hallucination candidate selection loop | 2026-09-10 | Model must select from returned register rows, never invent codes |
| DEC-005 | Strict Telegram verification gate (`tasdiqlangan` / `tasdiqlanmagan`) | 2026-09-10 | Prevents publishing unverified or misinterpreted figures |
| DEC-006 | Scoped cache identical-call guard | 2026-09-10 | Prevents token and roundtrip exhaustion during long PDF processing |
| DEC-007 | English-core technical documentation | 2026-09-10 | Standardized with `ai-agro` and `gis-ai-dept` ecosystem |

See [decisions-log.md](decisions-log.md) for full records.

---

## Governance & Authorization

- **Legal Authority**: State Committee of Statistics **Order No. 56** dated July 3, 2026.
- **Federal Governance SSOT**: `gis-ai-dept/knowledge/regulations/` (Order No. 56 and AI Use Regulation).
- **Press Service Guidelines**: Agency Press Office fact-verification mandate.

---

## Current Project Status

- **Host Agent Runtime**: Operational FastAPI server exposing `/v1/chat`, `/v1/models`, `/v1/files`, `/health`.
- **Neo4j Graph**: Loaded via `scripts/restore_dump.ps1` with 2.2M observations and fulltext index `indicator_fulltext`.
- **Toolsets**: 3 active custom plugins (`plugins/pressreliz`, `plugins/pdfmd`, `plugins/telegram`).
- **AI-First Baseline**: AGENTS routing table, L1/L2/L3 trust structure, automated unit test suite (`tests/`).

---

## Quick Links for AI Agents

- Neo4j Graph Schema Guide: [`knowledge/database/neo4j-graph-guide.md`](../database/neo4j-graph-guide.md)
- SIAT Verification Methodology: [`knowledge/methodology/siat-verification-methodology.md`](../methodology/siat-verification-methodology.md)
- Coordinator System Prompt: [`prompts/hermes_coordinator.md`](../../prompts/hermes_coordinator.md)
- Agent Persona: [`prompts/soul.md`](../../prompts/soul.md)
- Verification Suite: `python -m pytest tests/ -v --tb=short`
