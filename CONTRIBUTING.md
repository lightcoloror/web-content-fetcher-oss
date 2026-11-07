# Contributing

Updated: 2026-08-05 13:55:45 | Codex (GPT-5)

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m unittest discover -s tests -v
```

Install only the extras needed for the backend you are testing, for example
`pip install -e .[browser]`.

## Scope

Keep this repository a reusable acquisition layer. Site adapters, extraction,
quality diagnostics, neutral manifests, and archive indexes belong here.
Customer workflows, private taxonomies, editorial decisions, and downstream
business automation belong in caller projects.

## Safety

- Never commit credentials, cookies, storage state, browser profiles, private
  archives, screenshots, downloads, or customer data.
- Use environment variables and sanitized example configuration.
- Fixtures must be fictional or clearly public and redistributable.
- Do not add CAPTCHA bypass, paywall bypass, proxy-pool evasion, or access-control
  circumvention.
- New third-party integrations require a provenance row and license review.

## Checks

```powershell
python -m unittest discover -s tests -v
python -m pip wheel . --no-deps --wheel-dir output/wheels
git diff --check
```

Review `SECURITY.md`, `THIRD_PARTY_NOTICES.md`, and
`docs/dependency-provenance.md` before opening a pull request.
