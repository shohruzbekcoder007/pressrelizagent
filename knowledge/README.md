---
title: "Knowledge Base (L3): siat-verification"
description: AI-First Level 3 knowledge repository for SIAT verification engine
status: active
category: reference
tags: [ai-first, knowledge, siat, neo4j, verification]
last-verified: 2026-09-10
summary-ru: L3 База знаний для сервиса верификации пресс-релизов и индикаторов SIAT
related:
  - synthesis/context-brief.md
  - synthesis/decisions-log.md
  - database/neo4j-graph-guide.md
  - methodology/siat-verification-methodology.md
---

# L3 Knowledge Base: siat-verification

This directory contains the AI-First Level 3 knowledge base. All documents here are optimized for autonomous agent ingestion, reasoning, and context retrieval.

## Structure

- **`synthesis/`**: Fast cold-start briefings and architectural decision records (ADRs).
  - [context-brief.md](synthesis/context-brief.md): 60-second entry point for all agents.
  - [decisions-log.md](synthesis/decisions-log.md): Formal log of architecture and design decisions (DEC-001 through DEC-007).
- **`database/`**: Knowledge graph architecture and query guide.
  - [neo4j-graph-guide.md](database/neo4j-graph-guide.md): Graph schema, node properties, index configurations, and Cypher query templates.
- **`methodology/`**: Domain verification rules and heuristics.
  - [siat-verification-methodology.md](methodology/siat-verification-methodology.md): Rules for claim matching, temporal alignment, territorial breakdown, and publication gatekeeper thresholds.
- **`regulations/`**: Pointers to legal and agency-wide AI frameworks.
  - [README.md](regulations/README.md): Governance authorities and reference links.
