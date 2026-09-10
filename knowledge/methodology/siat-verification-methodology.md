---
title: "SIAT Verification Methodology: siat-verification"
description: Fact-checking methodology, tolerance thresholds, territorial alignment, and verification criteria for statistical releases
status: active
category: reference
tags: [ai-first, methodology, verification, siat, fact-check]
last-verified: 2026-09-10
summary-ru: Методология сопоставления фактов и верификации статистических релизов SIAT
summary-uz: SIAT statistik relizlarini tekshirish va faktlarni solishtirish metodologiyasi
related:
  - ../synthesis/context-brief.md
  - ../synthesis/decisions-log.md
  - ../database/neo4j-graph-guide.md
---

# SIAT Verification Methodology: siat-verification

This document specifies the official verification criteria used by `siat-verification` to validate draft press releases and media reports against the SIAT knowledge graph.

---

## 1. The Verification Workflow

A statistical claim within a draft document follows a 4-stage pipeline:

```
[Draft Release] ──> [Claim Extraction] ──> [Indicator Matching] ──> [Value Verification] ──> [Publishing Gate]
                       (pdf_extract)            (statind_code)           (statind_data)        (telegram_post)
```

1. **Claim Extraction**: Extract four mandatory parameters per claim:
   - Claimed subject / indicator name (`jumla` / `korsatkich`)
   - Claimed territory / administrative unit (`manzil` / `hudud`)
   - Claimed numeric value (`raqam`)
   - Claimed measurement unit (`birlik`)
2. **Indicator Matching**: Resolve candidate indicators using `statind_code`. Ensure the period type (annual vs quarterly vs monthly) matches the statement.
3. **Value Verification**: Query `statind_data` for the referenced period and territory. Compare the published value against the claimed figure.
4. **Publishing Gatekeeper**: Pass text to `telegram_post` with confirmed figures marked in `tasdiqlangan`.

---

## 2. Tolerance & Matching Rules

### 2.1 Period Alignment
- A monthly claim (`2024-yil yanvar-iyun`) must not be verified against an annual indicator.
- If a release cites year-to-date (cumulative), check whether the indicator is cumulative (`o'suvchi yakun bilan`) or discrete.

### 2.2 Numerical Precision and Rounding
- **Growth Rates & Percentages**: A tolerance of **±0.05%** is permitted for rounding (e.g., claimed `8.5%` matches official `8.47%`).
- **Nominal Monetary Values**: Rounding to nearest billion/million is permitted only if explicitly indicated in the text (e.g. "taxminan 12,4 trln so'm"). Otherwise, exact precision is required.

### 2.3 Unit Normalization
- SIAT database units are typically expressed in standard base units (e.g., `ming so'm`, `ming tonna`).
- The agent must perform explicit arithmetic scaling when the text uses `trln so'm` or `mlrd so'm`:
  - `1 trln so'm = 1,000,000,000 ming so'm`
  - `1 mlrd so'm = 1,000,000 ming so'm`

---

## 3. Discrepancy Classification

When a figure does not match, classify the discrepancy into one of four categories:

1. **Factual Error (`Xatolik`)**: The claimed figure disagrees with the published number beyond rounding tolerance (e.g., claimed `14.2%`, official `11.8%`).
2. **Period Mismatch (`Davr mos emas`)**: The figure is accurate, but belongs to a different period (e.g., 2023 instead of 2024).
3. **Territorial Ambiguity (`Hududiy noaniqlik`)**: The national figure was cited where a regional figure was implied, or vice versa.
4. **Unpublished / Unverified (`Tasdiqlanmagan`)**: The indicator has no recorded observation in the SIAT database (`has_observations: false`) or was not queried.
