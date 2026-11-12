from backend.app.core.config import settings
from backend.app.core.exceptions import ConfigurationError
from backend.app.llms.base import BaseLLMProvider
from backend.app.llms.gemini_ai import GeminiLLMProvider
from backend.app.llms.ollama_provider import OllamaLLMProvider
from backend.app.llms.openai_provider import OpenAILLMProvider


_PROVIDER_BUILDERS: dict[str, type[BaseLLMProvider]] = {
    "gemini": GeminiLLMProvider,
    "openai": OpenAILLMProvider,
    "ollama": OllamaLLMProvider,
}


def get_llm_provider() -> BaseLLMProvider:
    """Return the provider selected through environment settings."""

    provider_name = settings.llm_provider.strip().lower()
    provider_cls = _PROVIDER_BUILDERS.get(provider_name)

    if provider_cls is not None:
        return provider_cls()

    raise ConfigurationError(
        message=(
            f"Unsupported LLM provider: "
            f"{settings.llm_provider}"
        ),
        details={
            "supported_providers": list(_PROVIDER_BUILDERS.keys()),
        },
    )