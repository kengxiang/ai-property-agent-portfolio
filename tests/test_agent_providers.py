import pytest

from agent.graph import create_cloud_model


def test_gemini_requires_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        create_cloud_model()


def test_gemini_requires_model(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    with pytest.raises(ValueError, match="GEMINI_MODEL"):
        create_cloud_model()


def test_gemini_model_configuration(monkeypatch):

    captured = {}

    class FakeGemini:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setenv("GEMINI_MODEL", "test-model")

    monkeypatch.setattr(
        "agent.graph.ChatGoogleGenerativeAI",
        FakeGemini
    )

    create_cloud_model()

    assert captured["model"] == "test-model"
    assert captured["api_key"] == "fake-key"
    assert captured["temperature"] is None
    assert captured["timeout"] == 20
    assert captured["max_retries"] == 1
    assert captured["max_output_tokens"] == 2048
