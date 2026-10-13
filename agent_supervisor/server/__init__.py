"""Server side: needs ``pip install 'agent-supervisor-sdk[server]'``."""

from .core import Conflict, NotFound, Supervisor, SupervisorError

__all__ = ["Supervisor", "SupervisorError", "NotFound", "Conflict", "create_app"]


def create_app(*args, **kwargs):
    from .app import create_app as _create_app
    return _create_app(*args, **kwargs)
