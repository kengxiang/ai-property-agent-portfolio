from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from importlib import import_module

import httpx
import pytest
from langchain_core.messages import HumanMessage

from agent.graph import build_agent, create_cloud_model
from llm.gemini_rate_limit import GeminiRateWait, RollingGeminiLimit, track_gemini_http


def test_rolling_limit_under_concurrency_and_window_boundary():
    clock = [0]
    limiter = RollingGeminiLimit(clock=lambda: clock[0], sleep=lambda delay: None)
    limiter.max_wait = 0
    barrier = Barrier(8)

    def compete(_):
        barrier.wait()
        try:
            limiter.acquire()
            return True
        except GeminiRateWait:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(compete, range(8))) == 1
    for time in range(6, 60, 6):
        clock[0] = time
        limiter.acquire()
    clock[0] = 59
    with pytest.raises(GeminiRateWait) as blocked:
        limiter.acquire()
    assert blocked.value.retry_after_seconds == 1
    clock[0] = 60
    limiter.acquire()
    assert len(limiter.starts) == 10
    assert list(limiter.starts)[0] == 6


def test_gemini_http_retries_share_provider_cooldown():
    clock, requests = [0], []
    limiter = RollingGeminiLimit(clock=lambda: clock[0], sleep=lambda delay: clock.__setitem__(0, clock[0] + delay))

    def send(request):
        requests.append(clock[0])
        if len(requests) == 1:
            return httpx.Response(429, headers={"Retry-After": "24"},
                                  json={"error": {"details": [
                                      {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "30s"},
                                  ]}})
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(send),
                      event_hooks={"request": [limiter.before_send], "response": [limiter.after_response]}) as client:
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent"
        assert client.post(url).status_code == 429
        with pytest.raises(GeminiRateWait) as blocked:
            client.post(url)
        assert blocked.value.retry_after_seconds == 30
        clock[0] = 29
        assert client.post(url).status_code == 200
        assert requests == [0, 30]


def test_real_sdk_hooks_count_tool_continuation_with_mocked_gemini(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-unit-test")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    clock, requests = [0], []
    limiter = RollingGeminiLimit(clock=lambda: clock[0], sleep=lambda delay: clock.__setitem__(0, clock[0] + delay))
    monkeypatch.setattr("llm.gemini_rate_limit.shared_limiter", lambda: limiter)
    model = create_cloud_model()
    http_client = model.client._api_client._httpx_client
    http = import_module(next(cls.__module__.split(".")[0] for cls in type(http_client).__mro__
                              if cls.__module__.split(".")[0] in {"httpx", "httpx2"}))

    def reply(request):
        requests.append(clock[0])
        part = ({"functionCall": {"name": "get_property_details", "args": {"property_id": "P022"}},
                 "thoughtSignature": "dGVzdA=="}
                if len(requests) == 1 else {"text": "P022 costs RM220,000."})
        return http.Response(200, json={"candidates": [{
            "content": {"parts": [part], "role": "model"}, "finishReason": "STOP",
        }]})

    http_client._transport = http.MockTransport(reply)
    with track_gemini_http() as measurement:
        result = build_agent(model).invoke({"messages": [HumanMessage(content="What is the asking price of P022?")]})
    assert measurement["requests"] == 2
    assert result["messages"][-1].content == "P022 costs RM220,000."
    assert requests == [0, 6]


def test_task_budget_persists_and_stops_at_100(tmp_path):
    from llm.gemini_rate_limit import GeminiTaskStop, admit_budget

    budget = tmp_path / "budget"
    budget.write_text("99 open\n")
    admit_budget(budget)
    assert budget.read_text() == "100 open\n"
    with pytest.raises(GeminiTaskStop):
        admit_budget(budget)
    admit_budget(budget, stop=True)
    assert budget.read_text() == "100 closed\n"
