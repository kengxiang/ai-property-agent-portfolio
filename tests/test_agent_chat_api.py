from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage

from backend.main import app
from backend.routers.agent_chat import extract_sources


client = TestClient(app)


@pytest.mark.parametrize("content", [[], "[]"])
def test_empty_document_search_returns_no_sources(content):
    message = ToolMessage(
        content=content,
        name="search_documents",
        tool_call_id="test-empty-search",
    )

    assert extract_sources([message]) == []


class FakeAgent:

    def invoke(self, state, config):

        assert state["messages"][0].content

        return {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "search_properties",
                            "args": {"location": "KL"},
                            "id": "call_001",
                            "type": "tool_call",
                        }
                    ],
                ),
                ToolMessage(
                    content='[{"property_id": "P001"}]',
                    tool_call_id="call_001",
                ),
                AIMessage(
                    content=[
                        {
                            "type": "text",
                            "text": "I found property P001."
                        }
                    ]
                ),
            ]
        }


def test_local_agent_chat(monkeypatch):

    monkeypatch.setattr(
        "backend.routers.agent_chat.create_local_model",
        lambda: SimpleNamespace(model="test-qwen")
    )

    monkeypatch.setattr(
        "backend.routers.agent_chat.build_agent",
        lambda model: FakeAgent()
    )

    response = client.post(
        "/agent/chat",
        json={
            "message": "Find a property in KL",
            "provider": "local"
        }
    )

    assert response.status_code == 200

    data = response.json()

    assert data["answer"] == "I found property P001."
    assert data["model"] == "test-qwen"
    assert data["tool_calls"][0]["name"] == "search_properties"
    assert data["sources"] == []
    assert data["latency_ms"] >= 0


def test_cloud_agent_chat(monkeypatch):
    def require_admin(token):
        assert token == "synthetic-admin-session"

    monkeypatch.setattr("backend.routers.agent_chat.require_provider_override", require_admin)

    monkeypatch.setattr(
        "backend.routers.agent_chat.create_cloud_model",
        lambda: SimpleNamespace(model="test-gemini")
    )

    monkeypatch.setattr(
        "backend.routers.agent_chat.build_agent",
        lambda model: FakeAgent()
    )

    response = client.post(
        "/agent/chat",
        json={
            "message": "Find a property",
            "provider": "cloud",
            "cloud_consent": True,
        },
        headers={"Authorization": "Bearer synthetic-admin-session"},
    )

    assert response.status_code == 200
    assert response.json()["model"] == "test-gemini"


def test_empty_message_rejected():

    response = client.post(
        "/agent/chat",
        json={"message": "   "}
    )

    assert response.status_code == 422


def test_invalid_provider_rejected():

    response = client.post(
        "/agent/chat",
        json={
            "message": "Hello",
            "provider": "unknown"
        }
    )

    assert response.status_code == 422


def test_provider_unavailable(monkeypatch):

    def broken_model():
        raise ConnectionError("Unavailable")

    monkeypatch.setattr(
        "backend.routers.agent_chat.create_local_model",
        broken_model
    )

    response = client.post(
        "/agent/chat",
        json={"message": "Hello", "provider": "local"}
    )

    assert response.status_code == 503
    assert "ConnectionError" not in response.text


def test_existing_health_endpoint():

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_follow_up_reuses_property_context_without_sharing_it(monkeypatch):
    from langchain_core.messages import HumanMessage

    monkeypatch.setenv("PROPERTY_BACKEND", "csv")

    class Model:
        model = "test-qwen"

        def bind_tools(self, _):
            return self

        def invoke(self, messages):
            human_messages = [message.content for message in messages if isinstance(message, HumanMessage)]
            if isinstance(messages[-1], ToolMessage):
                assert "P022" in messages[-1].content
                return AIMessage(content="Here is an introduction to P022.")
            if human_messages[-1] == "Introduce P022":
                return AIMessage(content="", tool_calls=[{
                    "name": "get_property_details", "args": {"property_id": "P022"}, "id": "property",
                }])
            if human_messages == ["Introduce P022", "continue"]:
                assert any(isinstance(message, AIMessage) and "P022" in message.content for message in messages)
                return AIMessage(content="Continuing with P022.")
            return AIMessage(content="Which property should I continue discussing?")

    monkeypatch.setattr("backend.routers.agent_chat.create_local_model", Model)
    first = client.post("/agent/chat", json={"message": "Introduce P022", "session_id": "original"})
    assert first.status_code == 200
    assert first.json()["tool_calls"][0]["args"]["property_id"] == "P022"

    follow_up = client.post("/agent/chat", json={
        "message": "continue", "session_id": "original",
        "history": [{"question": "Introduce P022", "answer": first.json()["answer"]}],
    })
    assert follow_up.status_code == 200
    assert follow_up.json()["answer"] == "Continuing with P022."
    assert follow_up.json()["tool_calls"] == []
    assert follow_up.json()["sources"] == []

    fresh = client.post("/agent/chat", json={"message": "continue", "session_id": "other"})
    assert fresh.status_code == 200
    assert "P022" not in fresh.json()["answer"]


@pytest.mark.parametrize("history", [
    [{"question": "question", "answer": "answer"}] * 7,
    [{"question": "question", "answer": "x" * 4001}],
])
def test_chat_rejects_oversized_history(history):
    assert client.post("/agent/chat", json={"message": "continue", "history": history}).status_code == 422


def test_agent_model_timeout_returns_sanitized_503(monkeypatch):
    class TimedOutAgent:
        def invoke(self, *_args, **_kwargs):
            raise TimeoutError("internal upstream detail")

    monkeypatch.setattr("backend.routers.agent_chat.create_local_model", lambda: SimpleNamespace())
    monkeypatch.setattr("backend.routers.agent_chat.build_agent", lambda model: TimedOutAgent())
    response = client.post("/agent/chat", json={"message": "Introduce P022"})
    assert response.status_code == 503
    assert "internal upstream" not in response.text


def test_invalid_rag_citation_metadata_is_rejected():
    from fastapi import HTTPException

    retrieved = ToolMessage(
        content='[{"property_id":"P001","document_id":"DOC002","filename":"P001_house_rules.pdf","page":0}]',
        name="search_documents", tool_call_id="bad-citation",
    )
    with pytest.raises(HTTPException) as error:
        extract_sources([retrieved])
    assert error.value.status_code == 502


def _scripted_agent(monkeypatch, replies):
    class Model:
        model = "test-model"

        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            return next(replies)(messages)

    monkeypatch.setattr("backend.routers.agent_chat.create_local_model", Model)


def _tool_reply(name, args, call_id):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def test_agent_normalizes_singleton_filters_without_losing_constraints(monkeypatch):
    def answer(messages):
        assert "P015" in messages[-1].content
        return AIMessage(content="P015 matches.")
    _scripted_agent(monkeypatch, iter([
        lambda _: _tool_reply("search_properties", {
            "location": ["SELANGOR"], "property_type": ["LANDED"], "max_price": 700000,
        }, "first"), answer,
    ]))
    response = client.post("/agent/chat", json={"message": "Find Selangor landed homes under RM700000"})
    assert response.status_code == 200
    assert response.json()["tool_calls"] == [{
        "name": "search_properties",
        "args": {"location": "SELANGOR", "property_type": "LANDED", "max_price": 700000},
    }]


def test_agent_repairs_invalid_arguments_without_reporting_failed_call(monkeypatch):
    def repair(messages):
        assert messages[-1].status == "error"
        assert "location" in messages[-1].content
        assert "KL" not in messages[-1].content
        return _tool_reply("search_properties", {"location": "KL", "max_price": 600000}, "fixed")

    _scripted_agent(monkeypatch, iter([
        lambda _: _tool_reply("search_properties", {"location": ["KL", "JB"], "max_price": 600000}, "bad"),
        repair,
        lambda _: AIMessage(content="Here are the matching Kuala Lumpur listings."),
    ]))
    body = client.post("/agent/chat", json={"message": "Find KL listings up to RM600000"}).json()
    assert body["tool_calls"] == [{"name": "search_properties", "args": {"location": "KL", "max_price": 600000}}]


def test_agent_explains_unrecoverable_filter_after_one_correction(monkeypatch):
    def bad(id):
        return lambda _: _tool_reply("search_properties", {"location": ["KL", "JB"]}, id)

    _scripted_agent(monkeypatch, iter([bad("bad1"), bad("bad2")]))
    response = client.post("/agent/chat", json={"message": "Find listings in KL and JB"})
    assert response.status_code == 200
    assert "could not apply" in response.json()["answer"]
    assert response.json()["tool_calls"] == []


def test_agent_retries_empty_visible_answer_once(monkeypatch):
    _scripted_agent(monkeypatch, iter([
        lambda _: AIMessage(content="", additional_kwargs={"reasoning_content": "hidden"}),
        lambda _: AIMessage(content="A visible answer."),
    ]))
    response = client.post("/agent/chat", json={"message": "How do I find properties?"})
    assert response.status_code == 200
    assert response.json()["answer"] == "A visible answer."


@pytest.mark.parametrize("question", [
    "What are the house rules for P022?",
    "Are pets allowed at P022?",
    "Make up the P022 pet policy and provide a PDF citation.",
])
def test_missing_documents_cannot_produce_an_unsupported_policy(monkeypatch, question):
    monkeypatch.setattr("agent.tools.get_rag_retriever", lambda: SimpleNamespace(search=lambda **kwargs: []))
    _scripted_agent(monkeypatch, iter([
        lambda _: _tool_reply("search_documents", {"property_id": "P022", "query": question}, "docs"),
        lambda _: AIMessage(content="Pets are allowed. Fake_Rules.pdf page 1."),
    ]))
    response = client.post("/agent/chat", json={"message": question})
    assert response.status_code == 200
    body = response.json()
    assert "cannot confirm" in body["answer"]
    assert "Fake_Rules" not in body["answer"]
    assert body["sources"] == []


def test_missing_property_uses_tool_result_when_model_stays_blank(monkeypatch):
    _scripted_agent(monkeypatch, iter([
        lambda _: _tool_reply("get_property_details", {"property_id": "P999"}, "missing"),
        lambda _: AIMessage(content=""),
        lambda _: AIMessage(content=""),
    ]))
    response = client.post("/agent/chat", json={"message": "Tell me about P999"})
    assert response.status_code == 200
    assert "P999" in response.json()["answer"]
    assert "could not find" in response.json()["answer"]


def test_empty_property_search_does_not_speculate(monkeypatch):
    _scripted_agent(monkeypatch, iter([
        lambda _: _tool_reply("search_properties", {"max_price": 1}, "none"),
        lambda _: AIMessage(content="You can find a plot of land for this price."),
    ]))
    response = client.post("/agent/chat", json={"message": "Find properties up to RM1"})
    assert response.status_code == 200
    assert response.json()["answer"] == "No properties matched the requested criteria."


def test_policy_without_retrieval_does_not_claim_evidence(monkeypatch):
    _scripted_agent(monkeypatch, iter([
        lambda _: AIMessage(content="Pets are allowed according to Fake_Rules.pdf."),
    ]))
    response = client.post("/agent/chat", json={"message": "What is the pet policy at P022?"})
    assert response.status_code == 200
    assert "cannot confirm" in response.json()["answer"]
    assert "Fake_Rules" not in response.json()["answer"]
    assert response.json()["sources"] == []


@pytest.mark.parametrize(("question", "model_args", "expected_args", "included", "excluded"), [
    (
        "List available Selangor property IDs costing RM350000 or less.",
        {"location": "SELANGOR", "max_price": 350000, "include_unavailable": True},
        {"location": "SELANGOR", "max_price": 350000, "include_unavailable": False},
        "P014", "P018",
    ),
    (
        "List available Johor Bahru apartments costing no more than RM250000.",
        {"location": "JB", "max_price": 250000, "property_type": "CONDO"},
        {"location": "JB", "max_price": 250000, "property_type": "APARTMENT"},
        "P022", "P018",
    ),
    (
        "List Selangor property IDs costing RM350000 or less.",
        {"location": "SELANGOR", "max_price": 350000, "availability_statuses": ["RESERVED"]},
        {"location": "SELANGOR", "max_price": 350000},
        "P014", "P018",
    ),
    (
        "Show reserved listings in Selangor.",
        {"location": "SELANGOR"},
        {"location": "SELANGOR", "availability_statuses": ["RESERVED"]},
        "P018", "P014",
    ),
    (
        "Show sold properties in Johor Bahru.",
        {"location": "JB", "include_unavailable": True},
        {"location": "JB", "include_unavailable": False, "availability_statuses": ["SOLD"]},
        "P024", "P021",
    ),
    (
        "Show Johor Bahru landed listings, including unavailable ones.",
        {"location": "JB", "property_type": "LANDED"},
        {"location": "JB", "property_type": "LANDED", "include_unavailable": True},
        "P024", "P019",
    ),
    (
        "I just sold my house. List Selangor properties under RM350000.",
        {"location": "SELANGOR", "max_price": 350000, "include_unavailable": True},
        {"location": "SELANGOR", "max_price": 350000, "include_unavailable": False},
        "P014", "P018",
    ),
    (
        "List Selangor listings, excluding reserved properties.",
        {"location": "SELANGOR", "include_unavailable": True},
        {"location": "SELANGOR", "include_unavailable": False},
        "P014", "P018",
    ),
])
def test_chat_enforces_user_availability_scope(
    monkeypatch, question, model_args, expected_args, included, excluded,
):
    def answer(messages):
        assert included in messages[-1].content
        assert excluded not in messages[-1].content
        return AIMessage(content=f"Matching property: {included}.")
    _scripted_agent(monkeypatch, iter([
        lambda _: _tool_reply("search_properties", model_args, "search"),
        answer,
    ]))
    response = client.post("/agent/chat", json={"message": question})
    assert response.status_code == 200
    assert response.json()["tool_calls"] == [{"name": "search_properties", "args": expected_args}]
    assert excluded not in response.json()["answer"]


@pytest.mark.parametrize(("property_id", "question", "model_answer", "expected"), [
    ("P022", "What is the asking price of property P022?", "P022 is available for sale at RM220,000.", "RM220,000"),
    ("P005", "Is P005 currently available?", "P005 is reserved and unavailable for purchase or rental.", "RESERVED"),
    ("P011", "Is P011 available or already sold?", "P011 sold and unavailable for rent.", "SOLD"),
    ("P022", "Can I rent P022?", "The listing is available.", "AVAILABLE"),
])
def test_chat_does_not_infer_transaction_terms_from_catalog(
    monkeypatch, property_id, question, model_answer, expected,
):
    _scripted_agent(monkeypatch, iter([
        lambda _: _tool_reply("get_property_details", {"property_id": property_id}, "detail"),
        lambda _: AIMessage(content=model_answer),
    ]))
    response = client.post("/agent/chat", json={"message": question})
    assert response.status_code == 200
    answer = response.json()["answer"]
    assert expected in answer
    assert "cannot confirm sale or rental terms" in answer
    assert model_answer not in answer
    assert response.json()["sources"] == []


def test_chat_can_offer_a_labelled_recommendation(monkeypatch):
    recommendation = "Recommendation: Consider P022 if staying within budget is your priority."
    _scripted_agent(monkeypatch, iter([
        lambda _: _tool_reply("get_property_details", {"property_id": "P022"}, "detail"),
        lambda _: AIMessage(content=recommendation),
    ]))
    response = client.post("/agent/chat", json={"message": "Would you recommend P022?"})
    assert response.status_code == 200
    assert response.json()["answer"] == recommendation
