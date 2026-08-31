# Security Model

## Secure defaults

- Loopback-only API and Ollama endpoints.
- No hardcoded tokens, phone numbers, webhooks, or API keys.
- No arbitrary shell endpoint and no use of `shell=True`. A small structured command runner exists only for explicitly allowlisted programs and always requires one-action approval. Git has a second subcommand allowlist that excludes destructive and remote operations.
- No privilege escalation, service installation, firewall modification, or startup registration.
- Online health notifications, node discovery, and remote transport disabled by default. Local health monitoring cannot send externally until separately opted in.
- Workspace-confined file access with size limits and hidden-path rejection.
- Exact existing-file edits require an expected match count, create a rollback backup before replacement, use an atomic write, and report content hashes.
- Live news reads are limited to fixed public RSS URLs, bounded response sizes, sanitized XML text, and a short local cache. Arbitrary URL fetching is not exposed.
- Explicit approval for writes and other state-changing capabilities.
- Every security-relevant decision recorded in the audit chain.
- Project records are display-only metadata. They cannot contain an executable, command, script, URL, or authority-conferring filesystem path.

## Approval classes

| Class | Examples | Default |
| --- | --- | --- |
| Read-only | list skills, health, telemetry, search indexed memory | allowed |
| Local low-risk | create task, store a memory supplied by the operator | allowed and audited |
| Workspace mutation | write or replace a file inside an approved root | approval required |
| Workstation action | exact UI Automation click or allowlisted structured command | approval required |
| External visualization | open one fixed World Monitor dashboard URL | approval required |
| External side effect | send a notification or submit a signed remote job | disabled until paired and approval required |
| Privileged/destructive | generic shell, account changes, disk/firewall/service control | not exposed |

Approval is contextual authorization for one bounded action. It is not a reusable blanket grant.

Approval claims are atomic: a pending request is claimed before execution and cannot be executed twice. Failures are attached to the same audited request.

## Coding boundary

The Software Engineer skill can read source in configured roots and can propose new files or exact
text replacements. The desktop app discovers only an adjacent `Source` checkout or an explicit
`JARVIS_SOURCE_ROOT`; it never treats its extracted `_internal` package as editable source. Every
write and every developer process launch is independently approved. Overwrite backups live under
the private JARVIS data directory. The runner cannot invoke a shell, execute inline Python, change
Git configuration, use destructive Git subcommands, contact a Git remote, publish, or deploy.

## Repository assurance

GitHub-hosted automation treats repository scanning as a separate boundary from Angerona's local
endpoint defense. Pull requests and pushes run compilation, Ruff, pytest, dependency auditing, and
full-history secret detection. Main-branch pushes and a weekly schedule also run CodeQL and OpenSSF
Scorecard analysis. Dependabot checks both Python packages and GitHub Actions weekly.

Every referenced GitHub Action is pinned to an immutable commit, workflow tokens default to
read-only repository access, and individual jobs receive only the additional permissions they need.
Angerona and local credentials are never exposed to hosted runners.

## Live briefing boundary

The Live Briefing skill can issue GET requests only to source URLs compiled into the application.
Redirects must remain on the same approved host. Feed bodies, item counts, fields, and cache lifetime
are bounded, and returned text is labeled untrusted. Opening a fixed dashboard is a separate approved
action; receiving a briefing never opens a browser automatically.

## Device federation

- Tailscale is the private transport boundary; direct public-Internet exposure is denied.
- Every federation request has a canonical Ed25519 signature covering identity, request metadata, and exact body digest.
- Public keys are allowlisted with fixed scopes and explicit revocation.
- Timestamps, nonces, and request IDs enforce freshness, replay rejection, and idempotency.
- Only `compute.chat`, `compute.embed`, `data.list`, `data.read`, and `admin.request` exist. There is no remote shell scope.
- Data paths must remain inside explicitly shared roots.
- An admin request can only enter the target's approval queue. A remote peer cannot approve it.

## Angerona boundary

Angerona is the primary endpoint-defense authority and is started independently. JARVIS may call only the eight allowlisted read-only MCP tools on loopback with a 32-byte-or-longer bearer token. JARVIS does not import Angerona internals, launch it, kill processes, isolate adapters, or claim an unsupported actions endpoint.

## Application firewall, IDS, and IPS

The API accepts loopback clients only in the shipping desktop baseline, enforces a 1 MB request limit and per-minute request limit, rejects suspicious paths and methods, and adds a strict content-security policy and browser-hardening headers. The settings flowchart shows live allowed, blocked, suspicious, and throttled counters. This application policy complements rather than replaces the Windows firewall and Angerona.

## System health alert boundary

System Health samples CPU, memory, JARVIS data-volume usage, uptime, and available local temperature sensors read-only. Missing or malformed temperature data remains unavailable and cannot create a false normal reading. Threshold transitions are persisted before online I/O, use recovery hysteresis and a cooldown, and generate fixed typed messages without caller-supplied text, URLs, mentions, targets, or commands. Telegram and Discord require an enabled configured route, explicit health-notification opt-in, and explicit route selection. The Web App entry is a local feed only; Signal is unsupported.

## Desktop globe boundary

Globe mode is retired. The desktop launcher creates only the full adaptive dashboard, forces an older saved globe preference off during startup, and exposes no native companion bridge to the dashboard.

## Privacy and clipboard boundary

Privacy mode prevents persisted memory, including pinned memory, from being injected into chat prompts. Common credential forms are masked before new semantic-memory and local-notification text is embedded, persisted, or logged. Clipboard access is Unicode text only, bounded to a validated character limit, approval-gated even though it is read-only, and never stored as clipboard history or included in audit details.

## Audit chain

Each audit record incorporates the previous record hash and a canonical form of its own security-relevant fields. Verification recomputes the chain from the beginning. Set `JARVIS_AUDIT_SECRET` to a private random value if keyed integrity is desired; never commit it.

## Reporting

If you find a vulnerability, do not include secrets or personal data in the report. Record the affected version, reproduction steps using non-sensitive fixtures, expected behavior, and actual behavior.
