"""Chat website adapters. Add a site by implementing ChatProvider and
registering its class in PROVIDERS."""
from __future__ import annotations

from urllib.parse import urlparse

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
from .gemini import GeminiProvider

# Classes, not instances: id, display_name, home_url and matches_url are
# class-level so the relay and UI can use them without a page context.
PROVIDERS: dict[str, type[ChatProvider]] = {
    ChatGPTProvider.id: ChatGPTProvider,
    GeminiProvider.id: GeminiProvider,
}
DEFAULT_PROVIDER_ID = ChatGPTProvider.id


def create_provider(
    provider_id: str = DEFAULT_PROVIDER_ID,
    context: ProviderContext | None = None,
) -> ChatProvider:
    try:
        provider_class = PROVIDERS[provider_id]
    except KeyError:
        raise ValueError(f"Unknown chat provider: {provider_id!r}") from None
    return provider_class(context)


def provider_id_for_url(url: str) -> str | None:
    """Return the registered provider that owns a page URL, if any."""
    for provider_id, provider_class in PROVIDERS.items():
        try:
            if provider_class.matches_url(url):
                return provider_id
        except Exception:
            continue
    return None


def is_supported_url(url: str) -> bool:
    return provider_id_for_url(url) is not None


def provider_site(provider_id: str = DEFAULT_PROVIDER_ID) -> str:
    """Return the host shown to users, such as chatgpt.com."""
    return urlparse(PROVIDERS[provider_id].home_url).hostname or ""


__all__ = [
    "Capability",
    "ChatGPTProvider",
    "ChatProvider",
    "DEFAULT_PROVIDER_ID",
    "DictationStartCancelled",
    "GeminiProvider",
    "NullProviderContext",
    "PROVIDERS",
    "ProviderContext",
    "ResponseSnapshot",
    "UsageLimitError",
    "create_provider",
    "is_supported_url",
    "provider_id_for_url",
    "provider_site",
]
