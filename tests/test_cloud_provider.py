from types import SimpleNamespace

import pytest

from llm.cloud_provider import GeminiProvider


class FakeModels:

    def generate_content(self, model, contents):

        assert model == "gemini-3.5-flash"
        assert contents == "Hello"

        return SimpleNamespace(text="Hello from Gemini")


def test_gemini_success():

    fake_client = SimpleNamespace(
        models=FakeModels()
    )

    provider = GeminiProvider(
        client=fake_client,
        model="gemini-3.5-flash"
    )

    answer = provider.chat("Hello")

    assert answer == "Hello from Gemini"


def test_empty_message_rejected():

    provider = GeminiProvider(
        client=object(),
        model="gemini-3.5-flash"
    )

    with pytest.raises(ValueError):
        provider.chat("")


def test_missing_api_key(monkeypatch):

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    monkeypatch.setattr(
        "llm.cloud_provider.load_dotenv",
        lambda *args, **kwargs: None
    )

    with pytest.raises(ValueError):
        GeminiProvider(model="gemini-3.5-flash")


def test_gemini_api_error():

    class BrokenModels:
        def generate_content(self, model, contents):
            raise ConnectionError("Network failed")

    fake_client = SimpleNamespace(
        models=BrokenModels()
    )

    provider = GeminiProvider(
        client=fake_client,
        model="gemini-3.5-flash"
    )

    with pytest.raises(RuntimeError):
        provider.chat("Hello")
