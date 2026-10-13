"""Run local commands with the project's .env and writable temporary directory.

    .venv/Scripts/python.exe scripts/local.py serve
    .venv/Scripts/python.exe scripts/local.py demo --http http://localhost:8000
    .venv/Scripts/python.exe scripts/local.py gemini --impossible --pause
"""

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def main():
    from dotenv import load_dotenv

    os.chdir(ROOT)
    load_dotenv(ROOT / ".env", override=False)
    temp = ROOT / ".local" / "tmp"
    temp.mkdir(parents=True, exist_ok=True)
    os.environ["TEMP"] = os.environ["TMP"] = str(temp)
    os.environ["PYTHONUNBUFFERED"] = "1"
    args = sys.argv[1:]
    if not args or args[0] not in {"serve", "demo", "gemini", "test"}:
        print("Usage: scripts/local.py {serve|demo|gemini|test} [arguments]")
        return 2
    action, extra = args[0], args[1:]
    if action == "gemini":
        if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
            print("Set GEMINI_API_KEY in the ignored .env file first.")
            return 2
        command = [sys.executable, "examples/gemini_agent.py", *extra]
    elif action == "test":
        command = [sys.executable, "-m", "pytest", *extra]
    else:
        command = [sys.executable, "-m", "agent_supra.cli", action, *extra]
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
