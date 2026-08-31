# Jarvis Architecture

Jarvis is a local-first personal operations fabric. The Windows build runs a loopback FastAPI service inside a native pywebview/Edge WebView2 shell, preserving the browser HUD exactly. Ollama provides local chat, embeddings, and native tool planning. Remote-node transport and outbound messaging remain disabled until explicitly paired.

## Trust zones

```text
Windows desktop HUD / browser (same host)
        |
        | loopback HTTP
        v
Jarvis API ------------------------------------------------------+
  |               |                 |              |             |
  v               v                 v              v             v
Skill catalog  Approval gate  Capability policy  Task engine  Telemetry
  |               |                 |              |             |
  +---------------+--------+--------+--------------+-------------+
                           |
                           v
                   Hash-chained audit log
                           |
              +------------+-------------+
              |                          |
              v                          v
      Ollama on loopback          SQLite local state
```

The packaged application stores mutable state outside the bundle in `%LOCALAPPDATA%\JARVIS`. The preferred action workspace is `%USERPROFILE%\Documents\JARVIS Workspace`, with `%LOCALAPPDATA%\JARVIS\Workspace` as the Windows folder-protection fallback; `F:\BRAIN DRIVE\_root` is an additional configured root only when it exists.

## Core boundaries

- The API binds to `127.0.0.1` by default. A non-loopback bind is rejected while local-only mode is enabled.
- Ollama receives user text, selected skill instructions, and bounded retrieved context. It does not receive unrestricted filesystem or shell access.
- File capabilities resolve paths before use and reject paths outside configured workspace roots. The Windows desktop adds an adjacent editable `Source` checkout, or an explicit `JARVIS_SOURCE_ROOT`, without treating the packaged `_internal` bundle as source.
- Mutating or higher-risk capabilities create an approval request. They do not run merely because a model asked.
- Existing code edits use exact text/count preconditions, atomic replacement, automatic rollback copies, and before/after SHA-256 identities. Stale or ambiguous edits fail closed.
- Windows UI clicks identify an exact window and UI Automation control; free-form coordinates and image matching are not execution primitives.
- Structured commands use `shell=False`, an executable allowlist, bounded arguments/output/time, and an approved working directory. Git is additionally limited to local inspection, staging, commits, and initialization; destructive and remote subcommands are rejected.
- Live briefings make bounded outbound reads only to fixed public RSS hosts. Dashboard launch uses fixed URLs and remains approval-gated.
- Audit records form a verifiable hash chain. The optional audit secret adds keyed integrity protection without being stored in configuration.
- Federation authentication uses canonical Ed25519-signed request envelopes, a public-key allowlist, freshness windows, nonce replay protection, request idempotency, revocation, and fixed capability scopes.
- Online health notification, remote-node transport, discovery, and cloud providers have safe disabled defaults. Local read-only health monitoring is enabled independently.

## Main subsystems

- **Reasoning:** Ollama chat and embedding clients with timeouts, model discovery, and explicit failure states.
- **Live briefing:** fixed world/finance RSS adapters, bounded parsing/cache, partial-source reporting, and approved dashboard launchers.
- **Coding:** exact source transformations, atomic writes, rollback backups, constrained toolchains, and local-only Git operations.
- **Skills:** named instruction/tool bundles that narrow the model's role and capability surface.
- **Memory:** bounded local ingestion, Ollama embeddings, SQLite storage, and cosine-similarity retrieval.
- **Operations:** tasks, inactivity/deadline checks, telemetry, persistent CPU/memory/disk/temperature threshold state, cooldown-deduplicated health alerts, node status, and least-loaded scheduling.
- **Control:** approval queue, capability registry, path policy, and tamper-evident auditing.
- **Experience:** one-page HUD for chat, nodes, tasks, memory, approvals, skills, and audit history.
- **Capability access:** a Ctrl+K searchable deck combines navigation, skills, contextual panels, and the live guarded-action catalog without executing a result merely because it was searched.
- **Projects:** metadata-only SQLite workspaces with constrained lifecycle transitions, large expandable cards, and no executable/path fields.
- **Desktop:** resizable pywebview/Edge WebView2 shell with a 640 by 480 responsive minimum on ordinary displays, local persistent storage, and file URL, download, default menu, and developer-tool access disabled. The retired Background Globe is not created at startup; JARVIS uses one full adaptive dashboard window.
- **Communications:** write-only DPAPI credentials, fixed provider contracts, read-only setup checks, and a typed internal health-alert adapter for explicitly selected Telegram or Discord routes; there is no arbitrary-message API.
- **Federation:** typed compute, embedding, explicit-root data, and admin-request schemas; admin requests remain target-approved.
- **Defense:** application firewall, IDS/IPS counters and flow visualization, plus read-only authenticated Angerona MCP visibility.

## Deferred adapters

Live Tailscale/Mac/phone transport, Signal, mDNS, DDC/CI, Wake-on-LAN, UPS control, vision permissions, and cloud models still require their platform software, credentials, and target-device deployment. Angerona is integrated only through its supported loopback read-only MCP contract and remains independently operated.
