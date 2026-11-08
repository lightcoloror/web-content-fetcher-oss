# Windows source-checkout UI launcher

Run `scripts/start-web-ui.ps1` only when you intend to start the local UI. It uses
the source checkout module `web_content_fetcher.ui`; the installed distribution
already has the separate `wcf-ui` command. This wrapper is not an installed entry.

```powershell
.\scripts\start-web-ui.ps1 -PythonExecutable "C:\path\to\venv\Scripts\python.exe" -UiArgs @('--host','127.0.0.1','--port','8780')
```

The default interpreter name is `python` resolved as an Application on PATH.
No installation, provider selection, service startup outside the requested UI,
profile, proxy, TLS or security configuration is changed. The wrapper temporarily
uses its own source root as cwd and PYTHONPATH, then restores the caller's cwd
and prior PYTHONPATH presence/value in `finally`. A launched Python's native
exit code is propagated; missing/non-application interpreters and absent native
status fail instead of reusing stale success. Dot-sourcing is not supported.

The synthetic contract check copies the wrapper into a new test-owned fake
checkout with a clearly marked Python `ui.py` stub. It never imports the real UI,
binds HTTP, fetches pages, opens a browser or calls a model. It covers native
0/7, a seeded stale exit, explicit/default Python, missing/non-native interpreter,
space paths, argument forwarding, cwd and PYTHONPATH restoration. It does not
prove real UI/account/network behavior or every PowerShell version/profile.

```powershell
powershell.exe -NoProfile -File tests/test_start_web_ui.ps1 -PythonExecutable "C:\path\to\venv\Scripts\python.exe" -OutputRoot "C:\path\to\NEW-synthetic-result"
```

The result directory must not exist. It is retained for inspection; this test
does not clean an existing directory, install packages, elevate or change policy.
On a permission/policy refusal stop and keep the original error; do not change
execution policy or try another host to bypass it. Old private machine configuration,
real source lists, credentials, manuscripts and historical outputs are not included.
