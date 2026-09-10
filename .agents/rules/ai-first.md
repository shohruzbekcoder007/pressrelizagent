# AI-First Development & Maintenance Rules

These rules define how AI agents (Antigravity, Cursor, Hermes) must operate within this infrastructure to ensure a self-healing, machine-readable, and autonomous environment.

## 1. Documentation as Single Source of Truth (SSOT)
- **YAML over Markdown**: For structured data, prefer YAML files (e.g., config manifests, schema definitions).
- **Zero-Stale-Docs**: Every code change or architecture mutation **must** be accompanied by an update to the relevant documentation (e.g., `AGENTS.md`, `knowledge/synthesis/context-brief.md`, `knowledge/synthesis/decisions-log.md`).
- **Secrets Hygiene**: Never store credentials (passwords, API keys, tokens) in documentation or code. Reference them as "from `.env`" or "from host config".

## 2. Autonomous Maintenance (The "Hermes" Principle)
- **Automation Levels**:
  - **Level 2 (Autonomous)**: Agents may automatically apply lint fixes, add unit tests, improve prompt formatting, and update documentation.
  - **Level 3 (Human Approval Required)**: Major upgrades (FastAPI major version bump, Neo4j schema breaking changes, API auth modifications) require a pre-reviewed Plan and explicit human "OK".
- **Idempotent Scripts**: All maintenance scripts must be idempotent. Running them multiple times should result in the same stable state.
- **Self-Healing**: Prefer configurations that auto-recover over manual instructions.

## 3. Planning & Execution
- **Checklist-Driven Progress**: Update state symbols (`- [ ]` → `[~]` → `[x]`) in plan files after every successful step.
- **Verification Gates**: Every plan MUST include concrete verification steps (e.g., `python -m pytest tests/`, health checks). Confirming "success" without a verification gate is a failure of the rule.

## 4. Personality & Memory Protection
- **Marker-Safe Edits**: Programmatic edits to persona files (`prompts/soul.md`, `prompts/hermes_coordinator.md`) must preserve identity boundaries and tone.
- **Byte-for-Byte Preservation**: Never delete user-authored guidelines or tool descriptions outside explicit instructions.

## 5. AI-Native Code & Security
- **No Hardcoded Secrets**: Use `.env` or system environment variables. Audit all scripts for leaked keys before commitment.
- **Self-Documenting Code**: Write scripts and tools with docstrings, schemas, and typed outputs for cross-agent consumption.
- **Traceability**: Every script and config change must be searchable and linked to a decision in `knowledge/synthesis/decisions-log.md`.

## 6. Failure Behavior
- **Halt and Report**: If a verification gate fails or an unexpected error occurs, **halt immediately**. Report the error, current state, and relevant logs.
- **No Blind Retries**: Do not attempt the same failing action more than twice without human intervention.
