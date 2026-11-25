import io
import json
import urllib.error

import pytest

from llm.local_provider import OllamaProvider


def test_chat_success(monkeypatch):

    monkeypatch.setenv("OLLAMA_MODEL", "qwen3.5:4b")
    provider = OllamaProvider()

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data)

        assert payload["model"] == "qwen3.5:4b"
        assert payload["stream"] is False
        assert payload["messages"][0]["role"] == "user"

        response = {
            "message": {
                "content": "Hello from Qwen"
            }
        }

        return io.BytesIO(json.dumps(response).encode())

    monkeypatch.setattr(
        "urllib.request.urlopen",
        fake_urlopen
    )

    answer = provider.chat("Hello")

    assert answer == "Hello from Qwen"


def test_empty_message_rejected():

    provider = OllamaProvider()

    with pytest.raises(ValueError):
        provider.chat("")


def test_connection_error(monkeypatch):

    provider = OllamaProvider()

    def fake_urlopen(request, timeout):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(
        "urllib.request.urlopen",
        fake_urlopen
    )

    with pytest.raises(RuntimeError):
        provider.chat("Hello")


def test_empty_response_rejected(monkeypatch):

    provider = OllamaProvider()

    def fake_urlopen(request, timeout):
        response = {
            "message": {
                "content": ""
            }
        }

        return io.BytesIO(json.dumps(response).encode())

    monkeypatch.setattr(
        "urllib.request.urlopen",
        fake_urlopen
    )

    with pytest.raises(RuntimeError):
        provider.chat("Hello")
