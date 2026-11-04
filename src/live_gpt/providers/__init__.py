"""Chat website adapters. Add a site by implementing ChatProvider and
registering it in PROVIDERS."""
from __future__ import annotations

from typing import Callable

from .base import (
    Capability,
    ChatProvider,
    DictationStartCancelled,
    NullProviderContext,
    ProviderContext,
    ResponseSnapshot,
    UsageLimitError,
)
from .chatgpt import ChatGPTProvider

ProviderFactory = Callable[[ProviderContext | None], ChatProvider]

PROVIDERS: dict[str, ProviderFactory] = {
    ChatGPTProvider.id: ChatGPTProvider,
}
DEFAULT_PROVIDER_ID = ChatGPTProvider.id


def create_provider(
    provider_id: str = DEFAULT_PROVIDER_ID,
    context: ProviderContext | None = None,
) -> ChatProvider:
    try:
        factory = PROVIDERS[provider_id]
    except KeyError:
        raise ValueError(f"Unknown chat provider: {provider_id!r}") from None
    return factory(context)


__all__ = [
    "Capability",
    "ChatGPTProvider",
    "ChatProvider",
    "DEFAULT_PROVIDER_ID",
    "DictationStartCancelled",
    "NullProviderContext",
    "PROVIDERS",
    "ProviderContext",
    "ResponseSnapshot",
    "UsageLimitError",
    "create_provider",
]
