"""Command line: ``agent-supra serve | migrate | create-user | prune | demo``."""

from __future__ import annotations

import argparse
import getpass
import os
import sys


def _settings():
    from .server.settings import Settings
    return Settings.from_env()


def cmd_serve(args) -> int:
    import uvicorn
    from .server import db
    db.migrate(db.make_engine(_settings().database_url))   # once, before workers start
    uvicorn.run("agent_supra.server.app:create_app", factory=True, host=args.host, port=args.port,
                workers=args.workers, proxy_headers=True, forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "*"),
                log_level="info")
    return 0


def cmd_migrate(_args) -> int:
    from .server import db
    version = db.migrate(db.make_engine(_settings().database_url))
    print(f"Database is at schema version {version}.")
    return 0


def cmd_create_user(args) -> int:
    from .server.accounts import Accounts
    from .server.core import Supervisor, SupervisorError
    sup = Supervisor(_settings().database_url)
    password = os.environ.get("AGENT_SUPRA_NEW_PASSWORD") or getpass.getpass("Password (10+ characters): ")
    try:
        _, workspace = Accounts(sup.engine).signup(args.email, password, args.workspace)
    except SupervisorError as exc:
        print(f"Couldn't create the user: {exc}", file=sys.stderr)
        return 1
    print(f"Created {args.email} with workspace {workspace}.")
    return 0


def cmd_prune(args) -> int:
    from .server.core import Supervisor
    days = args.days if args.days is not None else _settings().retention_days
    removed = Supervisor(_settings().database_url).prune(days)
    print(f"Removed {removed} run(s) older than {days} days.")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["demo"]:
        # Hand everything after "demo" to the demo's own parser (--observe, --report, ...).
        from .demo import main as demo_main
        return demo_main(argv[1:])
    if argv[:1] == ["claude-hook"]:
        from .claude_hook import main as hook_main
        return hook_main(argv[1:])
    if argv[:1] == ["watch"]:
        from .watch import main as watch_main
        return watch_main(argv[1:])
    parser = argparse.ArgumentParser(prog="agent-supra", description="Agent Supra server tools")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the web server (reads DATABASE_URL, BASE_URL, ...)")
    serve.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    serve.add_argument("--workers", type=int, default=int(os.environ.get("WEB_CONCURRENCY", "2")))
    serve.set_defaults(fn=cmd_serve)

    sub.add_parser("migrate", help="create or upgrade database tables").set_defaults(fn=cmd_migrate)

    user = sub.add_parser("create-user", help="create an account (useful with ALLOW_SIGNUP=false)")
    user.add_argument("email")
    user.add_argument("--workspace", help="workspace name")
    user.set_defaults(fn=cmd_create_user)

    prune = sub.add_parser("prune", help="delete runs older than the retention window")
    prune.add_argument("--days", type=int)
    prune.set_defaults(fn=cmd_prune)

    sub.add_parser("demo", help="run the two-agent demo locally (try: agent-supra demo --help)")
    sub.add_parser("claude-hook", help="receive a Claude Code hook payload on stdin")
    sub.add_parser("watch", help="supervise a command (try: agent-supra watch --help)")

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
