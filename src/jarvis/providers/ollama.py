"""Async client for Ollama's documented local HTTP API."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx


class OllamaError(RuntimeError):
    """A normalized Ollama availability or response error."""


class OllamaProvider:
    """Access local chat, embedding, and model-listing endpoints."""

    def __init__(
        self,
        base_url: str,
        chat_model: str,
        embed_model: str,
        timeout_seconds: float = 300,
        temperature: float = 0.2,
        context_window: int = 8192,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.chat_model = chat_model
        self.embed_model = embed_model
        self.temperature = temperature
        self.context_window = context_window
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 10)),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, **kwargs)
            response.raise_for_status()
            payload = response.json()
        except httpx.ConnectError as exc:
            raise OllamaError(
                f"Ollama is unavailable at {self.base_url}. Start Ollama and try again."
            ) from exc
        except httpx.TimeoutException as exc:
            raise OllamaError("Ollama did not respond before the configured timeout") from exc
        except httpx.HTTPStatusError as exc:
            try:
                detail = exc.response.json().get("error", exc.response.text)
            except ValueError:
                detail = exc.response.text
            raise OllamaError(f"Ollama returned {exc.response.status_code}: {detail}") from exc
        except (ValueError, TypeError) as exc:
            raise OllamaError("Ollama returned an invalid JSON response") from exc
        if not isinstance(payload, dict):
            raise OllamaError("Ollama returned an unexpected response shape")
        return payload

    async def health(self) -> bool:
        try:
            await self.list_models()
        except OllamaError:
            return False
        return True

    async def list_models(self) -> list[dict[str, Any]]:
        payload = await self._request("GET", "/api/tags")
        models = payload.get("models", [])
        return models if isinstance(models, list) else []

    async def chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        model: str | None = None,
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Complete a non-streaming chat using optional native Ollama tools.

        Some otherwise capable models (including certain llama3 builds) do not
        implement native function calling. If Ollama explicitly rejects tool
        metadata, this method safely retries as ordinary chat without tools.
        """
        selected_model = model or self.chat_model
        body: dict[str, Any] = {
            "model": selected_model,
            "messages": list(messages),
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.context_window,
            },
        }
        if selected_model.casefold().startswith("qwen3"):
            # Keep ordinary answers thoughtful, but make bounded tool routing responsive.
            # Ollama supports Qwen3 tool calls with thinking explicitly disabled.
            body["think"] = not bool(tools)
        if tools:
            body["tools"] = list(tools)
        try:
            payload = await self._request("POST", "/api/chat", json=body)
        except OllamaError as exc:
            lowered = str(exc).lower()
            if tools and any(
                token in lowered for token in ("tool", "function", "400", "unsupported")
            ):
                body.pop("tools", None)
                payload = await self._request("POST", "/api/chat", json=body)
                payload["tools_supported"] = False
            else:
                raise
        message = payload.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content", ""), str):
            raise OllamaError("Ollama chat response did not include a valid message")
        return payload

    async def stream_chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        model: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield validated Ollama chat fragments for cancellable local playback."""
        body = {
            "model": model or self.chat_model,
            "messages": list(messages),
            "stream": True,
            "options": {"temperature": self.temperature, "num_ctx": self.context_window},
        }
        try:
            async with self._client.stream("POST", "/api/chat", json=body) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    payload = json.loads(line)
                    message = payload.get("message")
                    if not isinstance(message, dict) or not isinstance(
                        message.get("content", ""), str
                    ):
                        raise OllamaError("Ollama stream returned an invalid message fragment")
                    yield {
                        "content": message.get("content", ""),
                        "done": bool(payload.get("done")),
                        "model": payload.get("model", body["model"]),
                    }
        except httpx.ConnectError as exc:
            raise OllamaError(
                f"Ollama is unavailable at {self.base_url}. Start Ollama and try again."
            ) from exc
        except httpx.TimeoutException as exc:
            raise OllamaError("Ollama streaming response timed out") from exc
        except (httpx.HTTPStatusError, json.JSONDecodeError) as exc:
            raise OllamaError("Ollama streaming response was invalid") from exc

    async def embed(
        self, inputs: str | Sequence[str], model: str | None = None
    ) -> list[list[float]]:
        values = [inputs] if isinstance(inputs, str) else list(inputs)
        if not values or any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("Embedding input must contain non-empty text")
        payload = await self._request(
            "POST",
            "/api/embed",
            json={"model": model or self.embed_model, "input": values},
        )
        embeddings = payload.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(values):
            raise OllamaError("Ollama embedding response had an unexpected shape")
        result: list[list[float]] = []
        for vector in embeddings:
            if not isinstance(vector, list) or not vector:
                raise OllamaError("Ollama returned an empty embedding")
            try:
                result.append([float(value) for value in vector])
            except (TypeError, ValueError) as exc:
                raise OllamaError("Ollama returned a non-numeric embedding") from exc
        return result
