"""
The providers, one adapter each. The rest of the bot never asks which
provider it is talking to: it asks an adapter what it does (`functions`,
`reads`, `moderates`, `reports_costs`) and has it do that.

Adding a provider: an adapter module here, listed in ADAPTERS; its kind in
ai.models.ProviderKind; then its key in AI → Providers and its models, with
their prices, in AI → Models. Users who choose it are offered exactly the
functions it declares and has a model for.
"""

from .anthropic import Anthropic
from .base import Adapter, AIError, Made, ProviderRefused, Usage
from .openai import OpenAI

ADAPTERS = {adapter.kind: adapter for adapter in (OpenAI(), Anthropic())}

__all__ = ["ADAPTERS", "AIError", "Adapter", "Made", "ProviderRefused", "Usage", "of"]


def of(provider) -> Adapter:
    """The adapter of a provider (a Provider row, or its kind)."""
    kind = getattr(provider, "kind", provider)
    try:
        return ADAPTERS[kind]
    except KeyError:
        raise AIError(f"no adapter for the provider {kind!r}") from None
