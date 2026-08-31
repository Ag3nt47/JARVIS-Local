# Blueprint Traceability

This matrix separates the coherent architecture requirements in sections 1-9 of `JARVIS.docx` from later suggestions and conversational remnants. A check mark means implemented and verified in this repository; a circle means implemented as a safe local adapter or simulation; a dash means intentionally deferred because it needs hardware, an external account, credentials, elevated permissions, or separate authorization.

| Blueprint capability | Baseline disposition | Acceptance evidence |
| --- | --- | --- |
| Local-first reasoning | Ollama-only; bounded context | Model discovery, native tool schema, explicit 8K context, and graceful timeout behavior |
| Dynamic AI routing | Local provider interface; cloud adapter deferred | Provider status is visible and failure is graceful |
| Advanced role specialization | Built-in skill packs | Skills API and HUD selector expose scoped capabilities |
| Semantic long-term memory | Local SQLite + Ollama embeddings | Ingest, search, and deletion tests |
| Private-session memory mode | Local prompt-context suppression and credential masking | Privacy preference, zero-retrieval chat tests, and masking tests for memory and notifications |
| Multi-domain task monitoring | Local task and inactivity engine | Quiet hours, thresholds, and deduplication tests |
| Due-time reminders | One-shot local deadline notifications | UTC-normalized task due times, persisted notification marker, and deadline-rearm tests |
| Node health and scheduling | Local telemetry plus configured-node registry | Health and node APIs; least-loaded selection tests |
| System status and warnings | Local monitor on; online delivery separately off by default | CPU/memory/disk/temperature APIs, persistent thresholds, hysteresis/cooldown tests, top-HUD warning state, and typed Telegram/Discord alert tests |
| Workstation commands | Allowlisted structured runner | `shell=False`, bounded arguments/output/time, approved roots, and one-action approval |
| Button clicking | Exact Windows UI Automation controls | Window/control enumeration and approval-gated click tests; no coordinate click primitive |
| Clipboard understanding | Bounded Unicode text read | Mandatory one-time approval, no history, bounded output, and no clipboard content in audit details |
| Human approval | One-action approval queue | Writes/external effects cannot bypass approval |
| Tamper-evident audit | Hash-chained SQLite log | Chain verification endpoint and mutation test |
| Messaging setup and health alerts | Routes disabled until configured; health delivery separately opted in | DPAPI write-only secrets, fixed-host checks, local Web feed, and no arbitrary-message endpoint |
| Native Windows HUD | pywebview/Edge WebView2 desktop build | Same responsive HUD packaged as `JARVIS.exe`, resizable to 640 by 480 on ordinary displays, with persistent local state |
| Capability search | Keyboard-first command palette | Ctrl+K deck searches navigation, panels, skills, and the live guarded action catalog |
| Project workspaces | Persistent metadata-only project service and expandable HUD cards | Create/list/update API tests, lifecycle enforcement, audit entries, and hover/focus/click access |
| Background Globe companion | Retired | Desktop startup creates only the adaptive dashboard and clears an older saved enabled state; the legacy settings schema remains load-compatible |
| mDNS discovery | Disabled adapter boundary | No unsolicited network advertising in baseline |
| Signed device federation | Authentication and typed capability core | Ed25519 envelopes, key scopes/revocation, freshness, replay, idempotency, and strict data/admin models |
| Tailscale Mac/phone transport | Prepared, not paired on this workstation | Settings expose real prerequisite state; requires Tailscale and target JARVIS deployment |
| Mac compute/data/admin | Typed requests; live target pending | Fixed compute/data schemas and mandatory target-local approval; no remote shell |
| Angerona active defense | Authenticated read-only MCP observer | Eight-tool allowlist; no process killing, adapter isolation, or unsupported external action capability |
| SMB/KVM/DDC/CI setup | Deferred | Hardware-specific and outside software build scope |
| Wake-on-LAN/UPS | Deferred | Requires hardware inventory and explicit enablement |
| 3-2-1 backup | Guarded restic wrapper | Dry-run default; execution requires configured repository |
| Voice conversation | HUD/globe microphone dictation and system speech | Explicit microphone permission, live level-only analyser, synchronized transcript drafts, speech-boundary waveform, and selectable spoken replies |
| Screen capture/vision | Deferred | Requires OS permissions and a vision-capable model |
| Cloud GPT provider | Deferred by operator decision | No OpenAI dependency or secret required |

## Completion definition

The software baseline is complete when:

1. Installation and startup work from a clean virtual environment.
2. Ollama health, chat, and embeddings are exercised against installed local models.
3. Every API route has positive and important negative-path tests.
4. The HUD loads without external network assets and handles an offline Ollama state.
5. Path confinement, approval enforcement, and audit-chain verification pass adversarial tests.
6. The Windows package opens the same HUD and the action console works against the packaged API.
7. Documentation explains safe operation and all intentionally deferred live integrations.
