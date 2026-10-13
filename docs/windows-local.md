# Local setup on this Windows laptop

The repository is `C:\Users\sanchit\Downloads\agent-supra\agent-supra` (there are two nested folders).
Python 3.12 and all development dependencies are installed in `.venv`.
These commands work in PowerShell without activating the environment:

```powershell
cd C:\Users\sanchit\Downloads\agent-supra\agent-supra
.venv\Scripts\python.exe scripts/local.py serve --host 127.0.0.1 --workers 1
```

The server was started in the background during setup. If port 8000 is already in use,
open the existing server at <http://localhost:8000> instead of starting a second copy.
Server logs are in `.local/server.stdout.log` and `.local/server.stderr.log`.

## Local account

Email: `sanchitp498@gmail.com`. The generated password is in `.local/credentials.json`.
The development API key is in `.env`. Both files are ignored by Git and excluded from
release artifacts. The API key is stored only as a hash in the application's database.
Do not upload these local files or include them in a screen recording.

## Real Gemini tests

Get a key at <https://aistudio.google.com/apikey>, then put it in `.env`:

```dotenv
GEMINI_API_KEY='your-key'
```

Keep the existing `AGENT_SUPRA_API_KEY` and `AGENT_SUPRA_URL` entries.
The local launcher reads `.env` for each command and uses a writable temporary directory.
Existing environment variables take precedence over `.env`.

```powershell
.venv\Scripts\python.exe scripts/local.py gemini
.venv\Scripts\python.exe scripts/local.py gemini --impossible --pause
.venv\Scripts\python.exe scripts/local.py gemini --impossible
.venv\Scripts\python.exe scripts/local.py gemini --impossible --budget 0.001
```

For the outage test, use `--fail-mode closed` to test fail-closed behavior without editing
the example. The default is `--fail-mode open`. Follow `testing-with-gemini.md` for
the full test procedure, including recovery and live Slack delivery.

If setting variables directly in PowerShell, use `$env:GEMINI_API_KEY = 'your-key'`.
`set NAME=value` is Command Prompt syntax, not PowerShell syntax.

## Scripted demo and tests

```powershell
.venv\Scripts\python.exe scripts/local.py demo
.venv\Scripts\python.exe scripts/local.py demo --http http://127.0.0.1:8000
.venv\Scripts\python.exe scripts/local.py test -q
```

The demo uses scripted responses and makes no paid model calls. Its displayed spend is
simulated. The HTTP version places the two runs in your dashboard. `supervisor_report.html`
is the standalone report from the local demo.

## Release artifacts

```powershell
.venv\Scripts\python.exe -m build
.venv\Scripts\python.exe -m twine check dist/*
```

Publishing requires your PyPI account and authentication. The existing archives in `dist`
have been built and checked; they have not been uploaded.
