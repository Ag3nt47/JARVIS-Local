# Communications setup-center security acceptance criteria

This document is an implementation and review gate for Signal, Telegram, Discord, and
the private JARVIS web client. It does not enable an adapter or authorize public
internet exposure.

## Current baseline

- JARVIS binds to `127.0.0.1`, and the application firewall rejects non-loopback
  clients. Outbound notifications are disabled and unimplemented.
- `signal-cli`, Java, and Tailscale are not currently available on the Windows host.
- The desktop bundle does not contain `signal-cli`, a Java runtime, or Tailscale.
- The existing `notifications` table and local log store notification text in
  plaintext. Remote message bodies must not be copied there by default.
- The global HTTP rate limit is not an adequate per-operator or per-provider quota.
  Requests forwarded by one local reverse proxy would also share its loopback IP.

## Configuration and secret handling

1. Persist only non-secret provider state: enabled flag, display label, routing
   priority, masked target, capability flags, and last health classification.
2. Keep Telegram bot tokens, Discord webhook URLs/tokens, Telegram webhook secrets,
   Discord application private material, private-web session keys, and Signal link
   URIs out of `settings.json`, SQLite, logs, audit details, exception text, URLs
   returned to the UI, exports, and diagnostics. Use environment variables or an OS
   credential store. A configured secret is exposed only as a boolean and optional
   last-four/hash fingerprint.
3. A secret input is write-only. Reloaded forms must be blank and offer explicit
   Replace and Clear controls; never use the live value as a placeholder.
4. Telegram API URLs embed the bot token and Discord webhook URLs embed the webhook
   token. Normalize exceptions before logging because HTTP client request objects can
   contain the full secret URL.
5. Treat phone numbers, chat IDs, Discord channel IDs, usernames, device names, and
   message bodies as private data. Audit a stable keyed target fingerprint or masked
   suffix, never the raw identifier or content.
6. Credential presence must not enable a provider. Global outbound enablement,
   provider enablement, and credential readiness are separate conditions.
7. Secret references are fixed by code per provider. Never accept an arbitrary
   environment-variable or credential-store key name from the UI: that would let a
   route read and exfiltrate unrelated process secrets.

## Outbound destination policy and SSRF

- Do not offer a generic webhook URL or arbitrary HTTP route.
- Telegram requests use a code-defined `https://api.telegram.org:443` origin only.
- Discord incoming-webhook requests accept only the documented HTTPS Discord webhook
  origin and exact `/api/webhooks/{numeric_id}/{token}` shape. Reject userinfo, query,
  fragment, alternate port, encoded separators, and non-canonical hosts.
- Disable redirects for all provider sends. An HTTP redirect is a failure, not a new
  destination. Use normal platform certificate verification and bounded connect,
  write, read, and total timeouts.
- Never send to an IP literal, loopback, link-local, multicast, private network,
  `.local`, or a hostname obtained from provider response data. If a future custom
  destination is unavoidable, allowlist both its canonical hostname and resolved IPs
  at connection time and defend against DNS rebinding.
- Signal integration must use a local process boundary. Prefer JSON-RPC over stdin or
  an owner-only local socket. Do not start the unauthenticated signal-cli HTTP/TCP
  daemon, and do not place a message or phone number on the subprocess command line.
  Launch with an argument list, `shell=False`, a fixed executable, a dedicated data
  directory outside the app bundle, bounded output, and a hard timeout.

## Inbound webhook and prompt validation

- Inbound communications are off by default. Do not add a blanket exception to the
  current loopback firewall. A public webhook listener and a private tailnet web app
  need separate, minimal exposure boundaries from the workstation API.
- Telegram webhook requests require an exact constant-time comparison of
  `X-Telegram-Bot-Api-Secret-Token`, a bounded raw body, JSON content type, an allowed
  update type, an allowed chat/sender, and durable `update_id` replay rejection.
- Discord event/interactions requests require Ed25519 verification of the timestamp
  concatenated with the exact raw body before JSON parsing, timestamp freshness,
  event-ID replay rejection, and a `401` for invalid signatures. Handle the required
  ping without creating a command or notification.
- Incoming Signal envelopes are untrusted even though signal-cli decrypted them.
  Allowlist account/source/group, accept only bounded text messages needed by the
  product, and ignore attachments, contacts, group mutations, receipts, reactions,
  stories, and link/provisioning material unless separately designed.
- Remote text is data, never trusted instructions. It may create a conversation or
  proposal, but it must not invoke tools, approve actions, run commands, click UI,
  alter routes, or change security settings without the existing target-local
  approval boundary.
- Validate webhook body length after reading the actual bytes. A `Content-Length`
  check alone does not cover chunked requests.

## Private web client boundary

- Tailscale Serve or another local reverse proxy must expose explicit private-web
  routes only, never the entire loopback port. The local app must not trust a user
  identity header merely because the TCP peer is `127.0.0.1`; direct local callers can
  spoof such headers unless the proxy boundary is separately authenticated.
- Require a paired, revocable browser session with Secure, HttpOnly, SameSite cookies,
  CSRF protection on state changes, strict origin checks, short idle lifetime, and a
  visible device/session list. Do not put bearer credentials in query strings or
  browser local storage.
- Remote web routes receive a smaller capability set than the desktop API. Exclude
  approvals, raw audit/notification history, filesystem data, shell/command actions,
  UI clicks, integration secrets, and administrative settings unless a separately
  signed and target-approved protocol is implemented.
- Keep Tailscale Funnel/public sharing disabled. A route being on a tailnet is not a
  substitute for application authorization and CSRF controls.

## Test-send, quotas, and audit

- A Test button causes outbound network activity even when it sends no visible
  message. It must use a read-only provider check (`getMe`, webhook metadata, local
  signal-cli account/daemon readiness, or private-web readiness), never Telegram
  `sendMessage` or Discord webhook execution. Show the exact provider and masked
  destination, require a user gesture, and apply a per-provider plus per-operator
  quota independent of the global application-firewall counter. A reasonable initial
  ceiling is three checks per provider per five minutes with an idempotency key.
- Prevent double-click/retry duplicates with a durable request ID. If a provider times
  out after accepting a request, classify delivery as `uncertain`; do not automatically
  resend through another provider.
- Audit configuration field names, enable/disable, credential replace/clear, test-send
  request ID, provider, masked target fingerprint, duration, outcome class, and remote
  status code where safe. Never audit a message, token, raw URL, phone/chat/channel ID,
  webhook signature, response body, or provider exception text.
- Return normalized errors such as `not_configured`, `authentication_failed`,
  `rate_limited`, `target_rejected`, `transport_unavailable`, and `delivery_uncertain`.
  The UI must not display raw subprocess stderr or remote response bodies.

## Route ordering and failover

- Routing priority is an ordered list of explicitly enabled providers, not a race.
  Health probes do not send messages and cannot silently alter the saved order.
- Fail over only for a pre-authorized route group whose destinations represent the
  same operator and content classification. Provider failover changes the privacy
  boundary and must be visible in setup.
- Authentication, invalid target, rejected payload, policy denial, and permanent 4xx
  errors stop the route. Rate limiting honors `Retry-After`; it does not immediately
  spray another provider. Ambiguous timeout/delivery state stops as `uncertain`.
- Use a circuit breaker for repeated transport failures, but require an explicit
  operator action to re-enable an adapter disabled for credential or policy failure.
- Record one parent delivery/request ID and individual attempt records so an operator
  can see which provider was attempted without exposing content.

## Packaging and readiness

- Telegram and Discord can reuse the packaged HTTP and Ed25519 dependencies, but their
  adapters and any dynamic imports must be exercised against the PyInstaller build.
- Do not auto-download or auto-register signal-cli. Detect a configured executable,
  validate the resolved absolute file and version, and report `not_installed`,
  `java_missing`, `not_linked`, `ready`, or `version_unsupported` separately.
- If signal-cli is bundled later, review its license and update process, choose either
  its native distribution or a pinned private JRE, place mutable account data outside
  `_MEIPASS`, restrict filesystem permissions, and test process cleanup/database locks.
- Telegram/Discord status must distinguish token configured from a successful bounded
  API check. A readiness check must not send a user-visible message.
- Test the frozen app with secrets absent, malformed, replaced, and cleared; redirects;
  private/loopback destinations; provider 401/403/404/429/5xx; timeout before and after
  write; duplicate request IDs; audit redaction; and repeated UI Test clicks.

## Release blockers

The communications setup center is not ready to claim working remote messaging until:

- every secret/identifier redaction assertion passes against settings, SQLite, log,
  audit, exception, HTTP response, and exported diagnostics;
- outbound Test endpoints have independent identity-aware quotas and idempotency;
- webhook raw-body authentication/replay tests pass, or inbound routes remain absent;
- the private web surface is path-scoped and independently authenticated rather than
  exposing the workstation API through a loopback proxy; and
- the packaged artifact reports the real Signal/Tailscale prerequisites instead of a
  configured preference as readiness.

## Primary implementation references

- [signal-cli JSON-RPC manual](https://github.com/AsamK/signal-cli/blob/master/man/signal-cli-jsonrpc.5.adoc)
- [signal-cli daemon manual](https://github.com/AsamK/signal-cli/blob/master/man/signal-cli.1.adoc)
- [Telegram Bot API and webhook secret header](https://core.telegram.org/bots/api)
- [Discord webhook-event Ed25519 validation](https://docs.discord.com/developers/events/webhook-events)
- [Discord webhook resource](https://docs.discord.com/developers/resources/webhook)
- [OWASP SSRF prevention guidance](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)
