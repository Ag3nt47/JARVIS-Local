from __future__ import annotations

import json

import httpx
import pytest

from jarvis.providers.ollama import OllamaError, OllamaProvider


async def provider_with_transport(handler) -> OllamaProvider:
    provider = OllamaProvider(
        "http://127.0.0.1:11434", "chat-model", "embed-model", timeout_seconds=1
    )
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        base_url=provider.base_url,
        transport=httpx.MockTransport(handler),
        timeout=1,
    )
    return provider


@pytest.mark.asyncio
async def test_ollama_success_paths_use_expected_payloads() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "chat-model"}]})
        body = json.loads(request.content)
        if request.url.path == "/api/chat":
            assert body["model"] == "override-model"
            assert body["stream"] is False
            assert body["options"] == {"temperature": 0.2, "num_ctx": 8192}
            return httpx.Response(
                200,
                json={"model": body["model"], "message": {"role": "assistant", "content": "ok"}},
            )
        assert request.url.path == "/api/embed"
        assert body == {"model": "embed-model", "input": ["one", "two"]}
        return httpx.Response(200, json={"embeddings": [[1, 0], [0.5, 0.5]]})

    provider = await provider_with_transport(handler)
    try:
        assert await provider.health() is True
        assert await provider.list_models() == [{"name": "chat-model"}]
        chat = await provider.chat([{"role": "user", "content": "hi"}], model="override-model")
        assert chat["message"]["content"] == "ok"
        assert await provider.embed(["one", "two"]) == [[1.0, 0.0], [0.5, 0.5]]
        assert [request.url.path for request in requests] == [
            "/api/tags",
            "/api/tags",
            "/api/chat",
            "/api/embed",
        ]
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_ollama_normalizes_connection_and_response_errors() -> None:
    def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    provider = await provider_with_transport(unavailable)
    try:
        assert await provider.health() is False
        with pytest.raises(OllamaError, match="unavailable"):
            await provider.list_models()
    finally:
        await provider.close()

    def server_error(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "model crashed"})

    provider = await provider_with_transport(server_error)
    try:
        with pytest.raises(OllamaError, match="500: model crashed"):
            await provider.chat([{"role": "user", "content": "hi"}])
    finally:
        await provider.close()

    def invalid_json(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    provider = await provider_with_transport(invalid_json)
    try:
        with pytest.raises(OllamaError, match="invalid JSON"):
            await provider.list_models()
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_ollama_retries_without_tools_when_model_rejects_them() -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return httpx.Response(400, json={"error": "model does not support tools"})
        return httpx.Response(
            200,
            json={"model": "chat-model", "message": {"role": "assistant", "content": "fallback"}},
        )

    provider = await provider_with_transport(handler)
    try:
        response = await provider.chat(
            [{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "system_health"}}],
        )
    finally:
        await provider.close()

    assert len(bodies) == 2
    assert "tools" in bodies[0]
    assert "tools" not in bodies[1]
    assert response["message"]["content"] == "fallback"
    assert response["tools_supported"] is False


@pytest.mark.asyncio
async def test_qwen3_chat_uses_thinking_for_plain_chat_but_not_tool_routing() -> None:
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"model": "qwen3:4b", "message": {"role": "assistant", "content": "ok"}},
        )

    provider = await provider_with_transport(handler)
    try:
        await provider.chat([{"role": "user", "content": "hi"}], model="qwen3:4b")
        await provider.chat(
            [{"role": "user", "content": "use a tool"}],
            model="qwen3:4b",
            tools=[{"type": "function", "function": {"name": "system_health"}}],
        )
    finally:
        await provider.close()

    assert captured[0]["think"] is True
    assert captured[1]["think"] is False


@pytest.mark.asyncio
async def test_ollama_validates_embedding_and_chat_shapes() -> None:
    def bad_embed(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [["not-a-number"]]})

    provider = await provider_with_transport(bad_embed)
    try:
        with pytest.raises(ValueError, match="non-empty text"):
            await provider.embed("")
        with pytest.raises(OllamaError, match="non-numeric"):
            await provider.embed("text")
    finally:
        await provider.close()

    def bad_chat(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": []})

    provider = await provider_with_transport(bad_chat)
    try:
        with pytest.raises(OllamaError, match="valid message"):
            await provider.chat([{"role": "user", "content": "hi"}])
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_ollama_stream_chat_yields_validated_fragments() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["stream"] is True
        content = (
            b'{"model":"chat-model","message":{"content":"Hel"},"done":false}\n'
            b'{"model":"chat-model","message":{"content":"lo"},"done":true}\n'
        )
        return httpx.Response(200, content=content)

    provider = await provider_with_transport(handler)
    try:
        fragments = [
            fragment
            async for fragment in provider.stream_chat(
                [{"role": "user", "content": "hi"}]
            )
        ]
    finally:
        await provider.close()

    assert [item["content"] for item in fragments] == ["Hel", "lo"]
    assert fragments[-1]["done"] is True
