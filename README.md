# JARVIS Local

JARVIS Local is a private Windows desktop assistant backed by Ollama. The packaged app renders the same adaptive HUD as the browser version and adds conversational chat, microphone dictation, spoken replies, local hand controls, spatial file search, live world and finance briefings, semantic memory with a private-session mode, persistent projects, due-time reminders, approval-gated clipboard understanding, guarded software engineering and workstation actions, device-federation settings, an authenticated phone voice gateway, and a live Angerona cyber-defense flow.

This project is under active development. Computer actions remain deliberately constrained and
human-approved; JARVIS is not an unrestricted shell or autonomous administrator.

## Run the Windows app

Build the current source once:

```powershell
.\scripts\bootstrap.ps1 -WithDevTools
.\scripts\build_desktop.ps1
```

Then open `dist\JARVIS\JARVIS.exe`. JARVIS keeps its writable state in `%LOCALAPPDATA%\JARVIS`. It prefers `%USERPROFILE%\Documents\JARVIS Workspace` and automatically falls back to `%LOCALAPPDATA%\JARVIS\Workspace` when Windows folder protection blocks the Documents location.

To copy the packaged app to `D:\J.A.R.V.I.S\App` and create or refresh the current user's `JARVIS` desktop shortcut:

```powershell
.\scripts\install_desktop.ps1
```

For browser development, run `.\scripts\run.ps1` and open [http://127.0.0.1:8765](http://127.0.0.1:8765).

## Local intelligence

The default runtime uses:

- Chat and tool planning: `qwen3:4b`
- Memory embeddings: `nomic-embed-text:latest`
- Ollama API: `http://127.0.0.1:11434`
- Context window: 8,192 tokens, explicitly bounded to avoid the model's 262K default exhausting this workstation

Model choices can be changed in `config/default.toml` or with the documented environment variables. GPT remains an optional future integration and no cloud credential is required.

## Voice and workstation actions

The HUD can transcribe microphone input when the installed WebView supports speech recognition and can speak replies using system voices. The Action Console provides three explicit capabilities:

- List visible Windows applications and controls through UI Automation.
- Click an exact named control in an exact named window after one-action approval.
- Run an allowlisted executable with a structured argument list after one-action approval.

Commands never use a shell. Executables are allowlisted, Python inline code and module execution are blocked, Git configuration injection is blocked, working directories are confined to approved roots, and output/time are bounded. This is intentionally not an unrestricted command prompt.

Select **Live Briefing** to request current world or finance headlines by voice or text. JARVIS reads only a fixed list of approved public RSS feeds, bounds every response, labels unavailable sources, and treats all returned content as untrusted data. Opening the World Monitor or Finance World Monitor is a separate externally visible action and always enters the approval queue first. The reference project's arbitrary URL reader and placeholder search tool are intentionally not included.

## Guarded software engineering

Select **Software Engineer** when asking JARVIS to inspect, modify, test, or build its own source or another program in an approved root. The desktop build automatically adds an adjacent `Source` directory to its allowed roots; set `JARVIS_SOURCE_ROOT` to choose another explicit source checkout. Other repositories can be placed in the JARVIS Workspace or configured Brain Drive root.

Existing files are changed through an exact-text precondition: the requested source fragment must match the expected number of times or the edit fails without changing the file. Every successful overwrite creates a rollback copy under `%LOCALAPPDATA%\JARVIS\code_backups` and reports before/after SHA-256 hashes. New files, exact edits, build/test processes, and local Git mutations remain one-action approvals; JARVIS cannot approve its own request.

The process runner supports bounded development commands such as Python scripts, `pytest`, Ruff, Node, Go, Java, Rust/Cargo, .NET, and a deliberately narrow Git subcommand set. It never invokes a shell. Destructive Git operations, remote fetch/pull/push, inline Python, command-level Git configuration, publication, deployment, and paths outside approved roots are blocked.

## Communications setup

Settings includes a Communications center for Signal, Telegram, Discord, and private Web App access. Every route starts disabled. The guided setup keeps provider credentials write-only, encrypts locally entered credentials with Windows DPAPI for the current user, masks saved destinations, and supports fixed environment-variable overrides for advanced deployments. Provider tests are throttled, read-only readiness checks and never send a visible message.

Signal currently detects a local `signal-cli` installation without running it. Telegram verifies bot identity with `getMe`; Discord reads bot or webhook identity; the Web App route validates only a loopback, private-LAN, or tailnet origin and is excluded from delivery failover. This release intentionally exposes no arbitrary remote-send endpoint, inbound webhook listener, Signal linking command, or public workstation API. The System Health monitor is the sole bounded outbound path: it can send fixed, generated threshold alerts through an explicitly enabled Telegram or Discord route only after health notifications are separately opted in.

## System health and warnings

Settings includes a System Health center for CPU, memory, the JARVIS data volume, host uptime, and supported temperature sensors. Local monitoring starts enabled with conservative thresholds, persistent alert history, recovery hysteresis, and cooldown deduplication. The top HUD reports normal, partial, warning, or unavailable status, and a newly observed warning produces one non-repeating in-app alert.

Online health notifications start disabled. Enabling them requires a configured and enabled Telegram or Discord route plus explicit route selection in System Health; saving settings and provider tests never send a message. The Web App is a local alert feed, Signal delivery is not implemented, and the app reports temperature as unavailable when Windows exposes no supported psutil or already-running LibreHardwareMonitor/OpenHardwareMonitor sensor provider. Missing sensor data is never displayed as a safe temperature.

## Adaptive dashboard and local hand control

Projects are persistent local metadata records with title, summary, status, priority, progress, next action, tags, color, and pin state. Large project cards expand on pointer hover, keyboard focus, or click/touch. Creating and editing a project never exposes a filesystem path, URL, executable, script, or command.

The full dashboard is now JARVIS's visual presence. It shows idle, listening, thinking, acting, defense, and emergency-stop states, and reveals Files, Data, Angerona, Memory, and Settings from a spoken request, pointer hover, keyboard focus, click, or supported gesture. Context panels can be pinned, resized, moved, dismissed, and reset. The old detachable desktop globe is retired and is no longer created by the desktop app; compatibility fields remain only so older saved settings load safely.

Hand control is opt-in and uses a locally served MediaPipe Hand Landmarker. Pointing moves a bounded HUD cursor, pinch selects or drags, two pinched hands zoom file results, two raised fingers scroll, three raised fingers switch context panels, and a sustained fist triggers the emergency stop. Landmark confidence, smoothing, pinch hysteresis, stability frames, rate limits, and dashboard bounds reduce accidental activation. Camera frames stay in volatile browser memory and are never recorded or uploaded by JARVIS. Mouse, touch, keyboard, and voice remain available.

The Spatial Files panel searches names only inside configured roots. It supports extension filtering and zoomable result cards. Revealing a result in Explorer is a separate approval-gated action; edits still require the Software Engineer skill's exact-match, backup, root, approval, and audit controls.

Press **Ctrl+K** anywhere in the dashboard to search JARVIS commands, panels, skills, and available guarded actions. The Capability Deck is keyboard accessible and also opens from the top bar or application menu.

## Privacy, clipboard, and reminders

Privacy mode is available under Interface settings. While enabled, stored and pinned memory is not added to chat context. Common API keys, bearer tokens, passwords, and secrets are masked before new memory or local-notification text is embedded, stored, or logged.

Clipboard reading is a one-time, bounded Windows action. It always enters the local approval queue, does not maintain clipboard history, and does not place clipboard content in the audit record. Tasks accept an optional local due time; JARVIS produces one local reminder when the deadline arrives and rearms the reminder only if the deadline changes.

## Phone voice gateway

The phone gateway accepts Twilio Voice webhooks only when it is explicitly enabled and fully configured. JARVIS validates the provider signature against an exact operator-configured public HTTPS base URL, then enforces a caller allowlist, a PBKDF2-hashed six-digit PIN, call and command limits, lockout, replay-resistant call IDs, and a ten-minute session boundary. Speech and keypad input are gathered with TwiML and replies come from the existing local Ollama assistant.

The JARVIS API remains bound to loopback. Put an operator-managed HTTPS reverse proxy or tunnel in front of only the `/api/phone/*` routes and preserve the configured public URL exactly. Phone sessions can request guarded work but can never approve it; JARVIS tells the caller to use the local dashboard. Provider secrets, PINs, and raw audio are not stored.

Create the PIN record interactively with `jarvis phone-pin-hash`; enter the resulting record as `JARVIS_PHONE_PIN_HASH`. The PIN itself is never accepted as a command-line argument or written to JARVIS settings.

## Windows, MacBook, and device federation

The settings UI includes private peer endpoints, compute sharing, explicit data roots, and admin-capable request queues. Federation uses signed typed capabilities rather than a generic remote shell. The authentication core covers the peer/key identity, method, path, timestamp, nonce, request ID, and body digest with Ed25519, then enforces freshness, replay rejection, idempotency, key revocation, and exact scopes.

Admin-capable mode means a signed request may enter the approval queue on the target device. It never grants silent administrator access, and target-local approval cannot be disabled. Live MacBook/phone connectivity still requires Tailscale and a paired JARVIS node on each device.

The configured Brain Drive root is `F:\BRAIN DRIVE\_root`. It stays unavailable in the HUD until that exact directory exists; JARVIS does not create it automatically.

## Cyber defense and Angerona

The settings page renders the working defensive chain:

`Angerona → Tailscale → peer authentication → app firewall → IDS → IPS → capability policy → target approval → action/data/compute → audit`

JARVIS applies a loopback application firewall, request limits, suspicious-request detection, rate blocking, hardened browser headers, scoped capabilities, approvals, and a hash-chained audit log. The Defense settings area exposes live Angerona installed/process/MCP/token-readiness status, sanitized alerts on explicit request, target-locked accessibility inspection, and versioned defensive tactics. Angerona remains the primary endpoint defense.

JARVIS can operate Angerona in observe, recommend, or approved-execute modes. Every UI input is resolved through an exact window title and exact accessible control, and the owning process must resolve to the configured `ANGERONA_ROOT` before and after the action. Focus, move-to-control, invoke, bounded text entry, allowlisted keys, and bounded scrolling are available; free coordinates, image matching, generic shell text, target ambiguity, and automatic tactic execution are not. Input actions and tactic changes always enter the local approval queue, and the fist/dashboard emergency stop pauses Angerona input immediately.

## Verify

```powershell
.\scripts\check.ps1
.\.venv\Scripts\jarvis.exe models
.\.venv\Scripts\jarvis.exe audit-verify
```

Detailed design and limitations are in `docs/ARCHITECTURE.md`, `docs/SECURITY.md`, `docs/SKILLS.md`, and `docs/TRACEABILITY.md`.

Contributor setup and release expectations are documented in `CONTRIBUTING.md` and
`docs/RELEASING.md`. The project is available under the MIT license in `LICENSE`.

FRIDAY capability attribution and adaptation notes are recorded in `NOTICE.md`.
