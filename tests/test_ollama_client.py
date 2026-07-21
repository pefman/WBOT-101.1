import httpx
import pytest

from airadio.clients.ollama import check_ollama, ollama_chat


@pytest.mark.asyncio
async def test_ollama_chat_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        body = request.read()
        import json

        data = json.loads(body)
        assert data["model"] == "test-model"
        assert data["stream"] is False
        assert data["messages"][0]["role"] == "system"
        assert data["options"]["num_gpu"] == 0
        return httpx.Response(
            200, json={"message": {"role": "assistant", "content": " Hello airwaves "}}
        )

    transport = httpx.MockTransport(handler)
    # Patch AsyncClient used inside ollama_chat by monkeypatching httpx.AsyncClient
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
async def test_check_ollama_down():
    result = await check_ollama("http://127.0.0.1:9", "nope", timeout=0.2)
    assert result["ok"] is False
