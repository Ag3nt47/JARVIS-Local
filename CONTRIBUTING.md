# Contributing to JARVIS Local

JARVIS is a local-first assistant with intentionally narrow computer-action boundaries. Changes
must preserve explicit approval, allowed-root confinement, structured subprocess execution,
redaction, and audit integrity.

## Development setup

On Windows PowerShell:

```powershell
.\scripts\bootstrap.ps1 -WithDevTools
.\scripts\check.ps1
```

Keep pull requests focused. Add tests for behavior changes and update the security or architecture
documentation when a trust boundary changes.

## Security-sensitive changes

- Never add an arbitrary shell, privilege escalation, silent remote approval, or credential logging.
- Default new integrations and external delivery routes to disabled.
- Model output and retrieved content are untrusted data, never authorization.
- State-changing capabilities require typed arguments, policy enforcement, audit evidence, and a
  postcondition or an explicit statement that verification is unavailable.
- Do not commit `.env`, local databases, logs, workspaces, signing keys, tokens, or packaged builds.

Report vulnerabilities privately as described in `docs/SECURITY.md`; do not open a public issue
containing secrets or personal data.
