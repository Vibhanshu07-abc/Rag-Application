import json

import httpx
import pytest

from backend.app.core.config import settings
from backend.app.core.exceptions import ConfigurationError, LLMProviderError
from backend.app.llms import OllamaLLMProvider, get_llm_provider


def test_factory_returns_ollama_provider_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "gemini_api_key", None)
    monkeypatch.setattr(settings, "ollama_base_url", "http://127.0.0.1:11434")
    monkeypatch.setattr(settings, "ollama_llm_model", "llama3.1:8b")

    provider = get_llm_provider()
    try:
        assert isinstance(provider, OllamaLLMProvider)
        assert provider.model_name == "llama3.1:8b"
        assert provider.base_url == "http://127.0.0.1:11434"
    finally:
        provider.close()


def test_ollama_provider_validates_config() -> None:
    with pytest.raises(ConfigurationError):
        OllamaLLMProvider(base_url="   ")

    with pytest.raises(ConfigurationError):
        OllamaLLMProvider(model_name="   ")


def test_ollama_provider_generate_sends_chat_request_and_returns_content() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "model": "qwen2.5:7b",
                "message": {
                    "role": "assistant",
                    "content": "  Grounded answer [S1].  ",
                },
                "done": True,
                "done_reason": "stop",
            },
        )

    client = httpx.Client(
        base_url="http://127.0.0.1:11434",
        transport=httpx.MockTransport(handler),
    )
    provider = OllamaLLMProvider(model_name="qwen2.5:7b", client=client)

    result = provider.generate(
        system_prompt="Answer using only the provided sources.",
        user_prompt="What is the policy?",
    )

    assert result == "Grounded answer [S1]."
    assert captured["method"] == "POST"
    assert captured["url"] == "http://127.0.0.1:11434/api/chat"
    assert captured["body"] == {
        "model": "qwen2.5:7b",
        "stream": False,
        "messages": [
            {"role": "system", "content": "Answer using only the provided sources."},
            {"role": "user", "content": "What is the policy?"},
        ],
        "options": {
            "temperature": settings.ollama_llm_temperature,
            "num_predict": settings.ollama_llm_max_output_tokens,
        },
    }
    client.close()


def test_ollama_provider_rejects_empty_prompts() -> None:
    client = httpx.Client(
        base_url="http://127.0.0.1:11434",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})),
    )
    provider = OllamaLLMProvider(client=client)

    with pytest.raises(LLMProviderError, match="system prompt"):
        provider.generate(system_prompt="   ", user_prompt="Hello")

    with pytest.raises(LLMProviderError, match="user prompt"):
        provider.generate(system_prompt="System", user_prompt="  ")

    client.close()


def test_ollama_provider_rejects_empty_or_non_text_response() -> None:
    responses = iter(
        [
            httpx.Response(200, json={"message": {"role": "assistant", "content": "   "}}),
            httpx.Response(200, json={"message": None}),
        ]
    )
    client = httpx.Client(
        base_url="http://127.0.0.1:11434",
        transport=httpx.MockTransport(lambda _: next(responses)),
    )
    provider = OllamaLLMProvider(client=client)

    with pytest.raises(LLMProviderError, match="empty response"):
        provider.generate(system_prompt="System", user_prompt="Hello")

    with pytest.raises(LLMProviderError, match="non-text response"):
        provider.generate(system_prompt="System", user_prompt="Hello")

    client.close()


def test_ollama_provider_does_not_retry_non_retryable_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ollama_llm_max_retries", 2)
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404, json={"error": "model 'missing' not found"})

    client = httpx.Client(
        base_url="http://127.0.0.1:11434",
        transport=httpx.MockTransport(handler),
    )
    provider = OllamaLLMProvider(model_name="missing", client=client)

    with pytest.raises(LLMProviderError) as exc_info:
        provider.generate(system_prompt="System", user_prompt="Hello")

    assert calls == 1
    assert exc_info.value.details["status_code"] == 404
    assert "not found" in exc_info.value.details["error_message"]
    client.close()


def test_ollama_provider_retries_transient_errors_and_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ollama_llm_max_retries", 2)
    monkeypatch.setattr(settings, "ollama_llm_initial_retry_delay_seconds", 0.01)
    monkeypatch.setattr("backend.app.llms.ollama_provider.time.sleep", lambda _: None)

    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectTimeout("timed out", request=request)
        if attempts == 2:
            return httpx.Response(503, json={"error": "loading model"})
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "Ready [S1]."}},
        )

    client = httpx.Client(
        base_url="http://127.0.0.1:11434",
        transport=httpx.MockTransport(handler),
    )
    provider = OllamaLLMProvider(client=client)

    assert provider.generate(system_prompt="System", user_prompt="Hello") == "Ready [S1]."
    assert attempts == 3
    client.close()


def test_ollama_provider_raises_after_exhausting_transport_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ollama_llm_max_retries", 1)
    monkeypatch.setattr("backend.app.llms.ollama_provider.time.sleep", lambda _: None)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.Client(
        base_url="http://127.0.0.1:11434",
        transport=httpx.MockTransport(handler),
    )
    provider = OllamaLLMProvider(client=client)

    with pytest.raises(LLMProviderError) as exc_info:
        provider.generate(system_prompt="System", user_prompt="Hello")

    assert exc_info.value.details["error_type"] == "ConnectError"
    client.close()
