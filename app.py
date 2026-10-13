"""ASGI entrypoint for Vercel's FastAPI runtime."""

from agent_supra.server.app import create_app

app = create_app()
