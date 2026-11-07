# Security Policy

## Reporting a vulnerability

Do not report vulnerabilities, credentials, browser profiles, cookies, customer
data, private-page archives, or screenshots in a public issue.

If this repository has GitHub Private Vulnerability Reporting enabled, use the
repository Security tab to submit a private report. Otherwise, contact the
repository maintainer privately through GitHub and include a minimal,
redacted reproduction. Do not include real tokens, API keys, cookies, login
state, personal data, or permissioned page content.

## Scope

This policy covers the current `main` branch of `web-content-fetcher`, its
CLI, MCP server, local Web UI, browser adapters, archive handling, and bundled
configuration. Reports about third-party sites, their access controls, or
content availability are out of scope unless the issue is caused by this
project's code.

## Handling local data

This project is designed for authorized, low-frequency access only. Keep API
keys in environment variables or an external secret store. Keep browser
profiles, storage-state files, cookies, captured archives, caches, and logs
outside version control. Before committing, review `git status` and confirm
that generated evidence and local credentials are not staged.

The project does not support bypassing login, CAPTCHA, paywalls, rate limits,
or platform access controls.

## Release checks

Before a public release or visibility change:

1. Run the full offline unit test suite and a package build.
2. Run a worktree and reachable-history secret scan without printing matched
   values.
3. Confirm ignored browser, archive, download, and authentication artifacts are
   not tracked.
4. Review `THIRD_PARTY_NOTICES.md` and `docs/dependency-provenance.md`.
5. Verify public fixtures contain no permissioned URLs or captured content.

A public repository may still process private content locally, but that content
and its authentication state must remain outside Git.
