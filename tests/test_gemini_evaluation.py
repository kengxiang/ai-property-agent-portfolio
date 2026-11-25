import json

import httpx
import pytest
from fastapi.testclient import TestClient
from google.genai import types
from google.genai.errors import ClientError
from langchain_core.exceptions import ModelRateLimitError

from agent.graph import create_cloud_model
from backend.main import app
from evaluation.cloud_summary import summarize
from evaluation.run import CloudPacing, DATASET, request_turn, smoke_cases, smoke_checks, stop_reason
from llm.gemini_metrics import failure


@pytest.mark.parametrize(("error", "expected"), [
    (httpx.ReadTimeout("private upstream failure"), (504, "provider_timeout")),
    (ModelRateLimitError("private upstream failure"), (429, "rate_limit")),
    (ClientError(403, {"error": {"message": "private upstream failure"}}), (503, "provider_configuration")),
])
def test_safe_gemini_errors(error, expected):
    assert failure(error) == expected


@pytest.mark.parametrize(("kind", "status", "code"), [
    (httpx.ReadTimeout, 504, "provider_timeout"),
    (ModelRateLimitError, 429, "rate_limit"),
    (RuntimeError, 503, "provider_error"),
])
def test_agent_returns_classified_error_without_provider_text(monkeypatch, kind, status, code):
    monkeypatch.setattr("backend.routers.agent_chat.require_provider_override", lambda _: None)

    def fail():
        raise kind("raw credential diagnostic")

    monkeypatch.setattr("backend.routers.agent_chat.create_cloud_model", fail)
    response = TestClient(app).post("/agent/chat", json={
        "provider": "cloud", "cloud_consent": True, "message": "Synthetic question",
    })
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert "raw credential diagnostic" not in response.text


def test_gemini_sdk_tool_round_trip_and_usage(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-demo")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setattr("backend.routers.agent_chat.require_provider_override", lambda _: None)
    model = create_cloud_model()
    seen = []

    def generate(self, *, model, contents, config):
        seen.append((contents, config))
        if len(seen) == 1:
            part = types.Part(
                function_call=types.FunctionCall(name="get_property_details", args={"property_id": "P022"}),
                thought_signature=b"synthetic-signature",
            )
        else:
            assert contents[-2].parts[0].thought_signature == b"synthetic-signature"
            tool_response = contents[-1].parts[0].function_response
            assert tool_response.name == "get_property_details"
            assert "220000" in json.dumps(tool_response.response)
            part = types.Part(text="P022 costs RM220,000.")
        return types.GenerateContentResponse(
            model_version="gemini-3.5-flash-lite-test",
            candidates=[types.Candidate(content=types.Content(role="model", parts=[part]), finish_reason="STOP")],
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=10, candidates_token_count=3, thoughts_token_count=2, total_token_count=15,
            ),
        )

    monkeypatch.setattr(type(model.client.models), "generate_content", generate)
    monkeypatch.setattr("backend.routers.agent_chat.create_cloud_model", lambda: model)
    reply = TestClient(app).post("/agent/chat", json={
        "provider": "cloud", "cloud_consent": True, "message": "What is the recorded asking price of P022?",
    })
    assert reply.status_code == 200
    assert reply.json()["answer"] == "P022 costs RM220,000."
    assert reply.json()["tool_calls"] == [{"name": "get_property_details", "args": {"property_id": "P022"}}]
    assert reply.json()["provider_usage"] == {
        "calls": 2, "calls_with_usage": 2, "input_tokens": 20, "output_tokens": 10,
        "model_versions": ["gemini-3.5-flash-lite-test"],
    }
    assert seen[0][1].temperature is None
    assert seen[0][1].http_options.timeout == 20000
    assert seen[0][1].http_options.retry_options.attempts == 1


def test_rate_limit_pacing_retries_once_and_keeps_original_error():
    requests = []
    responses = iter([429, 200, 429])

    def reply(request):
        requests.append(request)
        status = next(responses)
        data = {"answer": "Synthetic"} if status == 200 else {"detail": {"code": "rate_limit"}}
        return httpx.Response(status, json=data)

    policy = CloudPacing(interval=0, backoff=0)
    with httpx.Client(base_url="http://sandbox", transport=httpx.MockTransport(reply)) as client:
        recovered = request_turn(client, "cloud", "First", [], policy)
        assert recovered["status"] == 200 and recovered["previous_attempt"]["status"] == 429
        stopped = request_turn(client, "cloud", "Second", [], policy)
        assert stop_reason(stopped)
    assert len(requests) == 3
    assert json.loads(requests[0].content)["cloud_consent"] is True


def test_cost_summary_preserves_missing_usage_and_wrong_property_checks():
    dataset = json.loads(DATASET.read_text())
    assert len(smoke_cases(dataset)) == 5
    prior = {"status": 429, "error": "HTTP 429", "response": {"provider_error": "rate_limit"}}
    result = summarize({"runs": [{
        "id": "P3-31", "status": 200, "error": None, "previous_attempt": prior,
        "response": {"sources": [{"property_id": "P002"}], "provider_usage": {
            "calls": 2, "calls_with_usage": 2, "input_tokens": 1000, "output_tokens": 1000,
            "model_versions": ["model-version"],
        }},
    }]}, dataset)
    assert result["wrong_property_citation_cases"] == ["P3-31"]
    assert result["errors_by_safe_code"] == {"rate_limit": 1}
    assert result["chat_attempts_without_complete_usage"] == 1
    assert result["estimated_usd_for_reported_tokens_at_standard_paid_rates"] == pytest.approx(0.0028)


def test_smoke_checks_gate_unsupported_evidence():
    cases = {
        "BASIC": {"answer": "Hello."},
        "P3-10": {"answer": "P001 matches.", "tool_calls": [{
            "name": "search_properties", "args": {"location": "KL", "property_type": "CONDO",
                                                  "min_price": 540000, "max_price": 570000, "near_mrt": True},
        }]},
        "P3-17": {"answer": "RM220,000", "tool_calls": [{
            "name": "get_property_details", "args": {"property_id": "P022"},
        }]},
        "P3-31": {"answer": "Written approval is required. P001_house_rules.pdf, page 1.",
                  "sources": [{"document_id": "DOC002", "property_id": "P001", "page": 1}]},
        "P3-39": {"answer": "I cannot confirm it.", "sources": []},
    }
    runs = [{"id": case, "error": None, "response": body, "replayed_search_ids": ["P001"]}
            for case, body in cases.items()]
    assert all(smoke_checks(runs).values())
    cases["P3-39"]["answer"] += " See invented.pdf."
    assert smoke_checks(runs)["missing_evidence"] is False
    cases["P3-31"]["sources"][0]["property_id"] = "P002"
    assert smoke_checks(runs)["rag_citation"] is False
