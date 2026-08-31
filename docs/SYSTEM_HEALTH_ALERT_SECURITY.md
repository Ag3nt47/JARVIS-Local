# System Health alert security and reliability criteria

This checklist governs CPU, memory, disk, temperature, process, and Ollama health
monitoring. It does not authorize a generic messaging endpoint, public API exposure, or
remote command execution.

## Safe defaults and boundaries

- Monitoring and online delivery are separate switches. Online delivery is disabled by
  default and remains disabled when a provider credential merely exists.
- A health sample is read-only. It cannot run a shell command, alter a threshold, approve
  an action, or supply arbitrary message text or an arbitrary destination URL.
- Existing `POST /api/notifications` is local-log only. It must not become a back door to
  the online health-alert adapter.
- Online alerts may use only an explicitly enabled, fixed communications provider route.
  Telegram and Discord retain their fixed vendor origins, certificate verification,
  bounded timeouts, redirects disabled, and environment-proxy bypass.
- Signal remains unavailable for automatic health alerts until its local process boundary,
  account linkage, and delivery behavior are separately implemented and tested.

## Threshold validation

- Reject booleans, strings, NaN, infinity, null, unknown fields, and empty patches wherever
  a numeric threshold is required.
- Percentage thresholds are finite values in a documented closed range. Temperature uses
  degrees Celsius and a separate physically reasonable range. Sampling interval, required
  consecutive samples, recovery hysteresis, and cooldown each have explicit lower and upper
  bounds to prevent tight loops or alerts that can never recover.
- Validate cross-field relationships. Recovery must occur below the trigger threshold;
  hysteresis cannot be negative or consume the entire valid range.
- Threshold changes reset or deliberately re-evaluate affected state without synthesizing
  a recovery or duplicate breach event.

## Edge triggering, recovery, and deduplication

- Model each metric/resource as an explicit state machine: `unknown`, `normal`, `breached`,
  and optionally `recovering`. Emit a breach only on the normal-to-breached transition after
  the configured number of valid consecutive samples.
- Repeated breached samples do not produce new alerts. If reminders are supported, one may
  occur only after the persisted cooldown and must retain the same incident identifier.
- Require hysteresis or consecutive healthy samples before emitting recovery. Values exactly
  on a boundary have one documented interpretation and tests on both adjacent sides.
- Use an atomic, durable dedupe key such as `health:{metric}:{resource}:{transition}`. Persist
  the last transition/attempt time before online I/O so double clicks, concurrent scheduler
  runs, retries, and process restarts cannot fan out duplicate alerts.
- A provider timeout after request transmission is `delivery_uncertain`; it must not be
  retried immediately through another provider. Provider authentication, target, payload,
  policy, and rate-limit errors stop rather than trigger failover.
- Use UTC timestamps for evidence but do not let a backwards wall-clock adjustment bypass a
  cooldown. Cap or safely reset corrupted/future persisted timestamps.

## Sensor and sampler behavior

- Missing or unsupported temperature sensors are `unavailable`, not zero, normal, or failed.
  They do not create an alert and do not prevent CPU, memory, disk, process, or Ollama checks.
- Ignore non-finite, malformed, stale, and impossible sensor values. A sensor read exception
  degrades that metric only; the scheduler continues and records a normalized local outcome.
- Name resources with bounded, sanitized labels. Do not put usernames, full filesystem paths,
  process command lines, host secrets, provider response bodies, or arbitrary sensor labels in
  online messages.
- Disk monitoring treats configured volumes independently and handles an unavailable/removable
  volume without reporting it as 100 percent used.
- Startup requires valid observations before transitioning to breached. A monitoring restart
  must not create a burst of duplicate online alerts.

## Safe online message content

- Build alerts from a fixed template and typed fields: metric enum, bounded display label,
  rounded observed value, unit enum, threshold, severity enum, transition, and timestamp.
  There is no API field for caller-supplied title, body, formatting, mentions, links, or embeds.
- Escape or eliminate provider markup. Disable Discord/TG mentions and link previews where
  supported. Bound the final encoded payload well below provider limits.
- Never include bot tokens, webhook URLs, authorization headers, phone/chat/channel IDs, raw
  hostnames, private IPs, file paths, audit hashes, exception text, or provider response bodies.
- Audit the incident ID, metric enum, transition, provider enum, normalized result, and safe
  remote status class. Do not audit the outbound URL, credential, target identifier, or full
  message body.

## Test and operational endpoints

- A status/readiness endpoint is read-only and secret-free. A manual sample endpoint, if
  exposed, is rate limited and cannot force a threshold breach or online delivery.
- A provider connectivity test remains the existing communications read-only identity check;
  it must not be repurposed into a visible test alert.
- Do not expose `/send`, `/broadcast`, arbitrary webhook, arbitrary message, threshold override,
  or caller-selected provider URL endpoints.
- Online delivery needs an independent persisted cooldown and idempotency key in addition to
  the loopback application-firewall rate limit. If a private phone UI is added, quotas must be
  keyed to authenticated operator/session identity rather than the reverse proxy IP.

## Release tests

- Exact threshold, just-below, and just-above values; booleans; NaN/infinity; negative and
  oversized intervals/cooldowns; invalid cross-field hysteresis.
- First breach, repeated breach, concurrent scheduler runs, cooldown expiry, recovery, re-breach,
  process restart, future/corrupt timestamps, and threshold changes.
- Missing/raising temperature APIs, empty sensor maps, impossible sensor values, missing disks,
  and independent continued evaluation of healthy metrics.
- Outbound disabled, provider disabled/unconfigured/unverified, 302 redirect, 401/403/404/429/5xx,
  DNS/connect/read timeout, and ambiguous request timeout.
- Secret and private-data redaction across API responses, SQLite, audit, logs, exceptions, HTTP
  client requests, frozen-app diagnostics, and UI error rendering.
- Frozen Windows build with no temperature sensor, no signal-cli, no Tailscale, and no provider
  credential must still start cleanly and remain local-only.

## Residual deployment gates

Online health alerts must not be described as production-ready until the adapter has durable
incident/attempt records, provider-specific idempotency behavior, safe payload tests, and
frozen-build validation. Private phone delivery additionally requires the separately
authenticated, path-scoped private web boundary; loopback proxying alone is not authentication.
