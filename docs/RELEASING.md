# Release process

Releases are built from reviewed version tags. The GitHub release workflow creates a Windows
portable ZIP but does not sign or publish an installer.

1. Run `.\scripts\check.ps1` on Windows and perform a desktop smoke test.
2. Update the version in `pyproject.toml` and `src/jarvis/__init__.py` together.
3. Review bundled third-party notices and generated artifacts.
4. Create and push a signed `v*` tag from a clean, reviewed commit.
5. Verify the GitHub Actions build and SHA-256 digest before publishing release notes.

Code signing is intentionally not automated until a protected signing identity and documented
key-custody process exist. Do not describe unsigned artifacts as signed.
