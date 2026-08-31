# Advanced Skill Packs

Jarvis skills are constrained operating modes. A skill combines a focused system prompt, a small capability allowlist, and optional memory context. Selecting a skill cannot grant a capability that the global security policy forbids.

## Built-in packs

- **Executive Assistant:** prioritization, deadlines, routines, and concise next actions.
- **Engineering & Debug:** evidence-first diagnosis, test planning, safe patch proposals, and rollback awareness.
- **Defensive Sentry:** telemetry interpretation, anomaly triage, containment recommendations, and incident notes. It cannot run offensive or destructive actions.
- **Research & RAG:** query decomposition, semantic retrieval, source reconciliation, and explicit uncertainty.
- **Document Analyst:** summaries, requirement extraction, contradiction checks, and action registers.
- **Live Briefing:** fixed-feed current world and finance headlines plus approval-gated visual dashboards.
- **System Health:** CPU, memory, storage, service, and node-status interpretation.
- **Software Engineer:** high-risk, approval-gated source inspection, exact code edits with rollback backups, and bounded local test/build commands. It cannot publish, deploy, run a shell, or use destructive/remote Git operations.
- **Safe Automation:** lists visible windows read-only, then queues exact UI Automation clicks and allowlisted structured commands for one-action approval.

## Skill contract

Every pack declares:

1. A stable identifier and human-readable description.
2. Instructions describing the role, method, and expected output.
3. Allowed capability names.
4. Whether long-term memory retrieval is appropriate.
5. A risk tier used by the approval policy and HUD.

Skill prompts are data. They cannot override the application policy, expand filesystem roots, expose secrets, select an unregistered executable, approve their own action, or bypass approval.

## Adding a skill

Add the pack through the skill registry and include tests that verify its identifier, tool scope, and risk tier. Prefer composition from existing safe capabilities over new direct operating-system hooks.
