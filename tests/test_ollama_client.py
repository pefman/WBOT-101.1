import json

import httpx
import pytest

from airadio.clients.ollama import check_ollama, ollama_chat, unload_model


@pytest.mark.asyncio
async def test_openai_compat_chat_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/chat/completions":
            data = json.loads(request.read())
            assert data["model"] == "test-model"
            assert data["stream"] is False
            assert data["messages"][0]["role"] == "system"
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"role": "assistant", "content": " Hello airwaves "}}
                    ]
                },
            )
        if request.url.path in ("/v1/models", "/api/tags"):
            return httpx.Response(200, json={"data": [], "models": []})
        return httpx.Response(404, json={"error": "not found"})

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    class Patched(real):
        def __init__(self, *a, **k):
            k["transport"] = transport
            super().__init__(*a, **k)

    import airadio.clients.ollama as mod

    orig = mod.httpx.AsyncClient
    mod.httpx.AsyncClient = Patched
    try:
        text = await ollama_chat(
            "http://127.0.0.1:11434",
            "test-model",
            "sys",
            "user",
            num_gpu=0,
        )
        assert text == "Hello airwaves"
    finally:
        mod.httpx.AsyncClient = orig


@pytest.mark.asyncio
async def test_unload_model_keep_alive_zero():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/ps":
            return httpx.Response(
                200, json={"models": [{"name": "qwen2.5:7b"}]}
            )
        if request.url.path == "/api/generate":
            body = json.loads(request.read())
            seen.append(body)
            assert body["keep_alive"] == 0
            assert body["model"] == "qwen2.5:7b"
            return httpx.Response(200, json={"done": True})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    class Patched(real):
        def __init__(self, *a, **k):
            k["transport"] = transport
            super().__init__(*a, **k)

    import airadio.clients.ollama as mod

    orig = mod.httpx.AsyncClient
    mod.httpx.AsyncClient = Patched
    try:
        await unload_model("http://127.0.0.1:11434", "qwen2.5:7b")
        assert seen and seen[0]["keep_alive"] == 0
    finally:
        mod.httpx.AsyncClient = orig


@pytest.mark.asyncio
async def test_check_ollama_down():
    result = await check_ollama("http://127.0.0.1:9", "nope", timeout=0.2)
    assert result["ok"] is False
