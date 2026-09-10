---
title: "Decisions Log: siat-verification"
description: Architectural Decision Records (ADRs) for the SIAT verification repository
status: active
category: reference
tags: [ai-first, architecture, decisions, adr, siat, neo4j, hermes]
last-verified: 2026-09-10
summary-ru: Журнал архитектурных решений (ADR) репозитория siat-verification
summary-uz: Loyihaning me'moriy qarorlar jurnali (ADR)
related:
  - context-brief.md
  - ../database/neo4j-graph-guide.md
  - ../methodology/siat-verification-methodology.md
---

# Decisions Log: siat-verification

Architectural Decision Records (ADRs) tracking foundational design choices for `siat-verification`.

---

## DEC-001: Dual-Backend Host Agent Runtime

- **Date**: 2026-09-10
- **Status**: Accepted
- **Context**: The real Hermes framework requires specific environment compilation and backend tool registration. If the container or build backend encounters environment discrepancies, the entire verification service would fail.
- **Decision**: Implement a dual-backend runtime in `agents/hermes_host.py`:
  1. `hermes`: Primary real Hermes `AIAgent` with plugins and native toolsets.
  2. `hermes_lite`: Fallback using LangGraph `create_react_agent` sharing the same tool schemas and system prompt.
- **Consequences**: Unbroken service availability with automated fallback during system re-provisioning.

---

## DEC-002: Neo4j Graph Retrieval Over Prompt Context-Stuffing

- **Date**: 2026-09-10
- **Status**: Accepted
- **Context**: The flat statistical indicator register consists of 3,326 rows, totaling over 146,710 tokens (~56% of a 262k context window). Dumping this into context costs ~52s latency on cold cache per call and causes high inference cost.
- **Decision**: Retain observations (2.2M nodes) and indicator definitions in Neo4j. Provide dynamic retrieval via `statind_code` and `statind_data`.
- **Consequences**: Query responses return in <1s, context usage is reduced to a few hundred tokens, and latency remains constant regardless of total indicators.

---

## DEC-003: Lucene Fulltext Search with Level 4 Leaf-Node Filtering

- **Date**: 2026-09-10
- **Status**: Accepted
- **Context**: The register contains 96 classifier headings (levels 1–3, e.g., `1.00.00 Iqtisodiy statistika`) which are non-measurable metadata categories. Naive keyword search often returns these headings instead of measurable indicators.
- **Decision**: Filter search results in `statind_code` to leaf rows (`level = 4`). Only explicit code lookup allows inspecting upper headings.
- **Consequences**: Eliminates false matches and prevents unmeasurable headings from crowding out measurable indicators in candidate lists.

---

## DEC-004: Anti-Hallucination Indicator Candidate Selection

- **Date**: 2026-09-10
- **Status**: Accepted
- **Context**: Classifier codes (`1.01.01.0001`) are regular, making LLMs prone to inventing plausible codes. An invented code cited in a published press release creates a serious official discrepancy.
- **Decision**: The tool `statind_code` returns candidate rows, and the agent must explicitly select from that candidate list. The tool never guesses, and the agent never invents codes from memory.
- **Consequences**: Guaranteed lineage between cited codes and database records.

---

## DEC-005: Telegram Publication Verification Gate

- **Date**: 2026-09-10
- **Status**: Accepted
- **Context**: Press release statements prepared for Telegram must not leak unverified claims into public channels.
- **Decision**: The `telegram_post` tool enforces verification: draft text must be submitted with `tasdiqlangan` (verified values read from `statind_data`). The tool checks every figure in the text and flags any `tasdiqlanmagan` (unverified) figures.
- **Consequences**: Operators and agents are blocked from publishing unverified numbers.

---

## DEC-006: Scoped Cache Identical-Call Guard

- **Date**: 2026-09-10
- **Status**: Accepted
- **Context**: Multi-turn document processing on long PDFs caused LLMs to repeat tool calls with identical parameters, exhausting turn budgets.
- **Decision**: Implement an in-turn call guard in `plugins/pressreliz/__init__.py` that returns cached responses with an explicit notification when a repeated call occurs.
- **Consequences**: Prevents infinite loops and roundtrip budget depletion.

---

## DEC-007: English-Core Documentation with Multilingual Human Summaries

- **Date**: 2026-09-10
- **Status**: Accepted
- **Context**: Aligns with organizational standards established in `ai-agro` and `gis-ai-dept`.
- **Decision**: All technical documentation bodies and frontmatter are written in English (the universal target for LLM tokenization efficiency). Localized human summaries are provided in `summary-ru` and `summary-uz`. Agent conversational responses to users default to Uzbek.
- **Consequences**: Clean tokenization, zero cross-model translation friction, and full compliance with ecosystem standards.
