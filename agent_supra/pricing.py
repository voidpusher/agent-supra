"""Model price table.

Money is stored as integer micro-dollars (1 USD = 1_000_000 micros) so sums
never drift the way floats do.

Built-in prices are Google's published Standard-tier Gemini prices, checked on
2026-10-13 (https://ai.google.dev/gemini-api/docs/pricing). Prices change:
override them with :func:`register_price`, the ``AGENT_SUPRA_PRICES``
environment variable on the server, or per-workspace prices in the dashboard.

Costs computed here are *estimates* from reported token usage, never your
provider invoice. Cached input is priced as normal input (an overestimate).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

PRICE_TABLE_VERSION = "2026-10-13"


@dataclass(frozen=True)
class Price:
    input_usd: float                  # per 1M input tokens
    output_usd: float                 # per 1M output tokens (incl. thinking)
    long_threshold: int | None = None  # prompts above this many tokens use the long rates
    long_input_usd: float | None = None
    long_output_usd: float | None = None


BUILTIN: dict[str, Price] = {
    "demo-model": Price(3.00, 15.00),
    # Gemini, Standard tier, text input. Source in the module docstring.
    "gemini-3.8-flash": Price(0.75, 3.75),
    "gemini-3.6-flash": Price(0.75, 3.75),
    "gemini-3.5-flash-lite": Price(0.30, 2.50),
    "gemini-3.1-pro-preview": Price(2.00, 12.00, 200_000, 4.00, 18.00),
    "gemini-3.1-flash-lite": Price(0.25, 1.50),
    "gemini-3-flash-preview": Price(0.50, 3.00),
    "gemini-2.5-pro": Price(1.25, 10.00, 200_000, 2.50, 15.00),
    "gemini-2.5-flash": Price(0.30, 2.50),
    "gemini-2.5-flash-lite": Price(0.10, 0.40),
}

_registered: dict[str, Price] = {}


def register_price(model: str, input_usd_per_mtok: float, output_usd_per_mtok: float,
                   long_threshold: int | None = None, long_input_usd: float | None = None,
                   long_output_usd: float | None = None) -> None:
    """Add or replace a model's price for this process (USD per million tokens)."""
    _registered[normalize(model)] = Price(input_usd_per_mtok, output_usd_per_mtok,
                                          long_threshold, long_input_usd, long_output_usd)


def parse_prices(raw: str | dict | None) -> dict[str, Price]:
    """Parse ``{"model": [input, output]}`` or ``{"model": {"input":..,"output":..}}``."""
    if not raw:
        return {}
    data = json.loads(raw) if isinstance(raw, str) else raw
    out: dict[str, Price] = {}
    for model, value in data.items():
        if isinstance(value, (list, tuple)):
            out[normalize(model)] = Price(float(value[0]), float(value[1]))
        else:
            out[normalize(model)] = Price(
                float(value["input"]), float(value["output"]), value.get("long_threshold"),
                value.get("long_input"), value.get("long_output"))
    return out


def normalize(model: str) -> str:
    model = (model or "").strip().lower()
    for prefix in ("models/", "publishers/google/models/"):
        if model.startswith(prefix):
            model = model[len(prefix):]
    return model


def lookup(model: str, overrides: dict[str, Price] | None = None) -> Price | None:
    """Exact match first, then the longest known prefix (``gemini-2.5-flash-001``
    resolves to ``gemini-2.5-flash``; ``gemini-2.5-flash-lite`` stays itself)."""
    name = normalize(model)
    tables = [overrides or {}, _registered, parse_prices(os.environ.get("AGENT_SUPRA_PRICES")), BUILTIN]
    for table in tables:
        if name in table:
            return table[name]
    for table in tables:
        matches = [k for k in table if name.startswith(k + "-") or name.startswith(k + "@")]
        if matches:
            return table[max(matches, key=len)]
    return None


def _micros(tokens: int, usd_per_mtok: float) -> int:
    return -(-tokens * round(usd_per_mtok * 1_000_000) // 1_000_000)  # ceil


def estimate_cost_micros(model: str, input_tokens: int, output_tokens: int,
                         overrides: dict[str, Price] | None = None) -> int | None:
    """Estimated cost in micro-dollars, rounded up. ``None`` if the model is unpriced."""
    price = lookup(model, overrides)
    if price is None:
        return None
    inp, out = price.input_usd, price.output_usd
    if price.long_threshold and input_tokens > price.long_threshold:
        inp = price.long_input_usd if price.long_input_usd is not None else inp
        out = price.long_output_usd if price.long_output_usd is not None else out
    return _micros(int(input_tokens), inp) + _micros(int(output_tokens), out)


def usd(micros: int | None, digits: int = 4) -> str:
    if micros is None:
        return "unpriced"
    return f"${micros / 1_000_000:,.{digits}f}"


def to_micros(amount_usd: float) -> int:
    return round(float(amount_usd) * 1_000_000)
