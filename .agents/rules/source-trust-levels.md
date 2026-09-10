# Source Trust Levels

## Trust Hierarchy

| Level | Directory | AI Access | Human Access | Description |
|---|---|---|---|---|
| **L1** | `source/` (schemas, regulations) | READ-ONLY | READ-ONLY | Official standards, data dictionaries, SDMX specs, DB dumps |
| **L2** | `source/meetings/` | READ + APPEND | READ + APPEND | Meeting notes, operator feedback logs |
| **L3** | `knowledge/` | READ + WRITE | READ + WRITE | AI-generated derivative guides, query recipes, synthesis docs |

## Conflict Resolution Rule

```
L1 > L2 > L3
```

When sources conflict, always trust the source with the lower level number.

## Mandatory Rules for AI Agents

### NEVER do
- ❌ Edit any files in `source/` directly.
- ❌ Modify meeting notes with `status: final`.
- ❌ Delete reference files or dumps.

### ALWAYS do
- ✅ Read `knowledge/synthesis/context-brief.md` first in a new session.
- ✅ Cite the source when creating L3 content:
  ```yaml
  derived-from: source/path/to/source-file
  ```
- ✅ Update `knowledge/synthesis/decisions-log.md` when architectural decisions are made.
- ✅ Update `knowledge/synthesis/context-brief.md` when project scope or status changes.
