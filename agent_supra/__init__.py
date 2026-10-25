"""Agent Supra: notice when an unattended agent is stuck, pause it, show why.

The SDK (this package's top level) has no dependencies. The server lives in
``agent_supra.server`` and needs ``pip install 'agent-supra[server]'``.
"""

__version__ = "1.1.0"

from .client import Client, SupervisorAPIError, SupervisorUnavailable  # noqa: E402
from .detector import DEFAULT_POLICY, Finding, evaluate  # noqa: E402
from .pricing import estimate_cost_micros, register_price  # noqa: E402
from .sdk import SupervisedRun, SupervisorPaused, error_fingerprint, fingerprint  # noqa: E402

__all__ = [
    "Client", "SupervisedRun", "SupervisorPaused", "SupervisorAPIError", "SupervisorUnavailable",
    "DEFAULT_POLICY", "Finding", "evaluate", "estimate_cost_micros", "register_price",
    "error_fingerprint", "fingerprint", "Supervisor", "SupervisorError",
]


def __getattr__(name):
    # Local, in-process supervisor (needs the server extra). Imported lazily so the
    # SDK stays dependency-free.
    if name in ("Supervisor", "SupervisorError"):
        from . import server
        return getattr(server, name)
    raise AttributeError(name)
