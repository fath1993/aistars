"""
The price of a request in credits, from what it costs you in USD.

    credits = ceil(cost ÷ Star value × (1 + margin ÷ 100)), at least 1

Margin and Star value are Runtime settings, editable in the admin (a model
can have its own margin); a change applies to the next price shown, never to
a price already held or paid.
"""

import math
from decimal import Decimal

from apps.core import runtime


def margin_percent() -> Decimal:
    return Decimal(str(runtime.get("PROFIT_MARGIN_PERCENT")))


def star_value_usd() -> Decimal:
    return Decimal(str(runtime.get("STAR_VALUE_USD")))


def credits_for_micros(cost_micros, model=None) -> int:
    """The same from micro-USD, with `model`'s own margin when it has one."""
    margin = model.margin if model is not None and model.margin is not None else None
    return credits_for(Decimal(int(cost_micros)) / 1_000_000, margin)


def credits_for(cost_usd, margin=None) -> int:
    """
    At least 1 credit for anything that costs something. `margin` (percent)
    replaces the general margin, for a model that has its own.
    """
    cost = Decimal(str(cost_usd))
    if cost <= 0:
        return 0
    margin = margin_percent() if margin is None else Decimal(str(margin))
    credits = cost / star_value_usd() * (1 + margin / 100)
    return max(1, math.ceil(credits))
