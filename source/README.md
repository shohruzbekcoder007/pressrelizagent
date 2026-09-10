# Source Directory (L1 / L2)

This directory houses Level 1 (L1) and Level 2 (L2) sources as defined in `.agents/rules/source-trust-levels.md`.

## Access Control & Trust Rules

- **L1 (READ-ONLY)**:
  - `schemas/`: Official data dictionaries, SDMX schema models, and database dump formats.
  - Files here are immutable external references. AI agents must NEVER modify or delete files in L1.
- **L2 (APPEND-ONLY)**:
  - `meetings/`: Human meeting minutes, review logs, and operator decision notes.
  - Editable while `status: draft`. Once marked `status: final`, files are frozen and immutable.
  - Always update `meetings/_index.yaml` when adding a meeting.

## Conflict Resolution

```
L1 > L2 > L3
```
In case of any discrepancy between knowledge base documents (L3) and sources here (L1/L2), the source with the lower number takes precedence.
