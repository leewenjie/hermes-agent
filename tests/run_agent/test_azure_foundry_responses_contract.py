import json
from types import SimpleNamespace

import httpx
import pytest

from agent.agent_runtime_helpers import create_openai_client


def test_azure_foundry_responses_outbound_contract():
    captured = {}

    def handle(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(
            500,
            json={"error": {"message": "intentional contract-test response"}},
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handle))
    agent = SimpleNamespace(
        provider="azure-foundry",
        _build_keepalive_http_client=lambda *_args, **_kwargs: None,
        _client_log_context=lambda: "azure contract test",
    )
    client = create_openai_client(
        agent,
        {
            "api_key": "azure-test-key",
            "base_url": "https://example-resource.cognitiveservices.azure.com/openai",
            "default_query": {"api-version": "2025-04-01-preview"},
            "http_client": http_client,
        },
        reason="contract-test",
        shared=False,
    )

    with pytest.raises(Exception):
        client.responses.create(
            model="example-deployment",
            input="ping",
            stream=True,
        )

    request = captured["request"]
    assert request.method == "POST"
    assert request.url.path == "/openai/responses"
    assert request.url.params["api-version"] == "2025-04-01-preview"
    assert request.headers["api-key"] == "azure-test-key"
    body = json.loads(request.content)
    assert body["model"] == "example-deployment"
    assert body["stream"] is True

    client.close()