# JARVIS Deferred Upgrades

Status: pinned and resumed on 2026-08-24.

This file is the durable, restart-safe source of truth for the requested JARVIS upgrades. It must remain readable without Ollama, a network connection, or the desktop shell.

## Product direction

- JARVIS is the primary assistant and the full dashboard is its presence. The old detachable pet/globe control is retired from the user interface; legacy settings may remain only for migration compatibility.
- JARVIS may inspect, explain, write, test, and maintain its own source code or other explicitly allowed projects through the existing exact-edit, backup, approval, root-boundary, and audit controls.
- Angerona is JARVIS's dedicated defensive endpoint. Defensive work should prefer Angerona observation, recommendation, and explicitly approved execution over generic desktop automation.

## Adaptive dashboard

- Show distinct idle, listening, thinking, acting, defense, and stopped states.
- Reveal contextual settings, telemetry, memory, files, and Angerona panels in response to voice intent, focus, hover, mouse interaction, or supported gestures.
- Panels can be pinned, resized, dismissed, and reset. Keyboard access, reduced motion, and a low-power mode are required.
- A visible emergency-stop state must immediately disable gesture-generated actions and cancel in-progress local interaction.

## Local hand controls

- Use the MIT-licensed ULTRON hand-tracking approach by Sagar Tamang as the interaction reference, with locally served MediaPipe runtime/model assets.
- Camera access is opt-in and requires an explicit browser permission. Frames remain in memory and are never recorded or uploaded by JARVIS.
- Supported interactions: point, pinch-to-select, pinch-and-drag, two-hand zoom, two-finger scroll, three-finger panel swipe, and fist emergency stop.
- Apply landmark confidence checks, smoothing, pinch hysteresis, rate limits, calibration, and strict dashboard target bounds. Mouse, touch, and keyboard always remain available.

## Spatial file search

- Search allowed local roots by voice, keyboard, mouse, or gesture and show zoomable, filterable result cards and previews.
- Point/pinch can select and drag result cards. Opening or revealing a file must remain root-bound, explicit, logged, and approval-gated where it changes external application state.
- File mutation remains restricted to the existing software-engineering capability with exact edits, backups, and approval.

## Angerona control and tactics

- Lock all control to a uniquely verified Angerona window and process before every input.
- Provide inspect, focus, move-to-control, invoke/click, text entry, keyboard, scroll, and search primitives without unrestricted coordinate clicking or shell text.
- Modes are observe, recommend, and approved execute. Mutating input requires single-use local approval, rate limiting, before/after evidence, and a hash-chained audit record.
- Never send an input to another window. Any target mismatch, ambiguity, focus change, or emergency stop aborts the operation.
- Persist versioned defensive tactics with bounded UI steps, stated preconditions, expected evidence, and operator enable/disable controls.

## Phone voice gateway

- Receive calls through Twilio webhooks exposed only through an operator-configured HTTPS reverse proxy to the loopback JARVIS API.
- Validate the exact Twilio signature against the configured public URL; enforce a caller allowlist, hashed PIN, replay resistance, lockout, call/command rate limits, and maximum session duration.
- Use speech/digit gather prompts and spoken replies, with the existing local Ollama assistant behind the gateway.
- Phone sessions cannot approve guarded or high-risk actions. Such requests are queued for local approval and explained to the caller.
- Record authentication and command outcomes without storing PINs, provider secrets, raw audio, or unnecessary transcript content.

## Delivery and verification

- Integrate the capabilities into settings, skills, tools, API, and the desktop build.
- Keep all features disabled by default when they require a camera, external service, or desktop control.
- Add unit and integration coverage for target locking, root boundaries, authentication/signature checks, replay/lockout, gesture state transitions, and approval enforcement.
- Run formatting/linting, the complete automated test suite, a production build, installation, and a smoke test before release.

## Existing capabilities that must not regress

- FRIDAY-inspired local voice interaction and live world/finance briefing.
- Guarded self-coding and other-program coding through explicit allowed roots.
- Local-first Ollama operation, memory retrieval, approvals, audit trail, security posture, system health, projects, communications, and federation controls.
