import time

import httpx

from backend.app.core.config import settings
from backend.app.core.exceptions import (
    ConfigurationError,
    LLMProviderError,
)
from backend.app.core.logging import get_logger
from backend.app.llms.base import BaseLLMProvider


logger = get_logger(__name__)


RETRYABLE_STATUS_CODES = {
    408,
    429,
    500,
    502,
    503,
    504,
}


class OllamaLLMProvider(BaseLLMProvider):
    """
    Local Ollama implementation of the application's LLM interface.

    Communicates with Ollama's ``/api/chat`` HTTP endpoint without requiring
    an external API key.
    """

    def __init__(
        self,
        *,
        model_name: str | None = None,
        base_url: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        resolved_base_url = (base_url or settings.ollama_base_url).strip().rstrip("/")
        if not resolved_base_url:
            raise ConfigurationError(
                message="OLLAMA_BASE_URL cannot be empty."
            )

        resolved_model_name = (model_name or settings.ollama_llm_model).strip()
        if not resolved_model_name:
            raise ConfigurationError(
                message="OLLAMA_LLM_MODEL cannot be empty."
            )

        self.base_url = resolved_base_url
        self.model_name = resolved_model_name
        self.max_retries = settings.ollama_llm_max_retries
        self.initial_retry_delay_seconds = (
            settings.ollama_llm_initial_retry_delay_seconds
        )
        self.max_retry_delay_seconds = settings.ollama_llm_max_retry_delay_seconds

        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self.base_url,
            timeout=settings.ollama_llm_timeout_seconds,
        )

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """Generate one text response using a local Ollama model."""

        normalized_system_prompt = system_prompt.strip()
        normalized_user_prompt = user_prompt.strip()

        if not normalized_system_prompt:
            raise LLMProviderError(
                message="The system prompt cannot be empty."
            )

        if not normalized_user_prompt:
            raise LLMProviderError(
                message="The user prompt cannot be empty."
            )

        logger.info(
            "ollama_generation_started",
            model=self.model_name,
            base_url=self.base_url,
            system_prompt_length=len(normalized_system_prompt),
            user_prompt_length=len(normalized_user_prompt),
        )

        payload: dict[str, object] = {
            "model": self.model_name,
            "stream": False,
            "messages": [
                {
                    "role": "system",
                    "content": normalized_system_prompt,
                },
                {
                    "role": "user",
                    "content": normalized_user_prompt,
                },
            ],
            "options": {
                "temperature": settings.ollama_llm_temperature,
                "num_predict": settings.ollama_llm_max_output_tokens,
            },
        }

        data = self._post_with_retries(payload=payload)

        message_obj = data.get("message")
        output_text = (
            message_obj.get("content")
            if isinstance(message_obj, dict)
            else None
        )

        if not isinstance(output_text, str):
            raise LLMProviderError(
                message="Ollama returned a non-text response.",
                details={
                    "provider": "ollama",
                    "model": self.model_name,
                    "done_reason": data.get("done_reason"),
                },
            )

        normalized_output = output_text.strip()

        if not normalized_output:
            raise LLMProviderError(
                message="Ollama returned an empty response.",
                details={
                    "provider": "ollama",
                    "model": self.model_name,
                    "done_reason": data.get("done_reason"),
                },
            )

        logger.info(
            "ollama_generation_completed",
            model=self.model_name,
            output_length=len(normalized_output),
        )

        return normalized_output

    def _post_with_retries(self, *, payload: dict[str, object]) -> dict[str, object]:
        """Call Ollama /api/chat and retry transient transport or server errors."""

        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.post("/api/chat", json=payload)

                if response.status_code >= 400:
                    error_message = self._extract_error_message(response)
                    should_retry = (
                        response.status_code in RETRYABLE_STATUS_CODES
                        and attempt < self.max_retries
                    )
                    if not should_retry:
                        logger.error(
                            "ollama_generation_failed",
                            model=self.model_name,
                            status_code=response.status_code,
                            error_message=error_message,
                        )
                        raise LLMProviderError(
                            details={
                                "provider": "ollama",
                                "model": self.model_name,
                                "base_url": self.base_url,
                                "status_code": response.status_code,
                                "error_message": error_message,
                            }
                        )

                    self._sleep_before_retry(
                        attempt=attempt,
                        status_code=response.status_code,
                        error_type=f"HTTP_{response.status_code}",
                    )
                    continue

                data = response.json()
                if not isinstance(data, dict):
                    raise LLMProviderError(
                        message="Ollama returned an unexpected JSON payload.",
                        details={
                            "provider": "ollama",
                            "model": self.model_name,
                        },
                    )
                return data

            except LLMProviderError:
                raise
            except httpx.HTTPError as exc:
                if attempt >= self.max_retries:
                    logger.error(
                        "ollama_generation_failed",
                        model=self.model_name,
                        base_url=self.base_url,
                        error_type=type(exc).__name__,
                    )
                    raise LLMProviderError(
                        details={
                            "provider": "ollama",
                            "model": self.model_name,
                            "base_url": self.base_url,
                            "error_type": type(exc).__name__,
                            "error_message": str(exc),
                        }
                    ) from exc

                self._sleep_before_retry(
                    attempt=attempt,
                    status_code=None,
                    error_type=type(exc).__name__,
                )
            except Exception as exc:
                logger.error(
                    "ollama_generation_failed",
                    model=self.model_name,
                    error_type=type(exc).__name__,
                )
                raise LLMProviderError(
                    details={
                        "provider": "ollama",
                        "model": self.model_name,
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    }
                ) from exc

        raise LLMProviderError(
            message="Ollama generation failed after all retry attempts.",
            details={
                "provider": "ollama",
                "model": self.model_name,
                "base_url": self.base_url,
            },
        )

    def _sleep_before_retry(
        self,
        *,
        attempt: int,
        status_code: int | None,
        error_type: str,
    ) -> None:
        delay = min(
            self.initial_retry_delay_seconds * (2**attempt),
            self.max_retry_delay_seconds,
        )
        logger.warning(
            "ollama_generation_retrying",
            model=self.model_name,
            attempt=attempt + 1,
            maximum_attempts=self.max_retries + 1,
            status_code=status_code,
            error_type=error_type,
            retry_delay_seconds=delay,
        )
        time.sleep(delay)

    @staticmethod
    def _extract_error_message(response: httpx.Response) -> str:
        """Extract an informative error message from an Ollama error response."""

        try:
            body = response.json()
        except ValueError:
            return response.text.strip() or f"HTTP {response.status_code}"

        if isinstance(body, dict) and isinstance(body.get("error"), str):
            return body["error"]
        return response.text.strip() or f"HTTP {response.status_code}"

    def close(self) -> None:
        """Close the owned HTTP client."""

        if self._owns_client:
            self._client.close()
