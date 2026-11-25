import hashlib
import json

import httpx
import pytest
from google.genai.errors import APIError

from evaluation.checkpoint import record, resume_checkpoint, save
from evaluation.run import CloudPacing, request_turn
from llm.gemini_metrics import error_metadata, retry_after_seconds


def test_resume_preserves_successful_rows_failed_attempts_and_source(tmp_path):
    ids = ["a", "b", "c"]
    original = {
        "provider": "cloud", "mode": "benchmark", "dataset_sha256": "digest",
        "backend_container": "container", "planned_cases": 3, "started_at": "earlier",
        "runs": [{"id": "a", "error": None}, {"id": "b", "error": "HTTP 429"}],
    }
    source = tmp_path / "original.json"
    source.write_text(json.dumps(original))
    before = source.read_bytes()
    resumed = resume_checkpoint(source, ids, "container", "digest")
    with pytest.raises(ValueError, match="completed"):
        record(resumed, {"id": "a", "error": None}, ids)
    record(resumed, {"id": "b", "error": None}, ids)
    output = tmp_path / "resumed.json"
    save(resumed, ids, output)
    result = json.loads(output.read_text())
    assert source.read_bytes() == before
    assert result["case_progress"] == {"planned": ids, "successful": ["a", "b"], "failed": [], "unattempted": ["c"]}
    assert result["prior_runs"] == [{"id": "b", "error": "HTTP 429"}]
    assert result["resumed_from"][0]["sha256"] == hashlib.sha256(before).hexdigest()
    with pytest.raises(ValueError, match="same dataset"):
        resume_checkpoint(source, ids, "other-container", "digest")
    migrated = resume_checkpoint(source, ids, "other-container", "digest", pacing_update=True)
    assert migrated["resumed_from"][0]["backend_container"] == "container"
    assert "pacing update" in migrated["transport_change"]
    original["runs"].append(original["runs"][0])
    source.write_text(json.dumps(original))
    with pytest.raises(ValueError, match="duplicate"):
        resume_checkpoint(source, ids, "container", "digest")


def test_retry_metadata_uses_only_sanitized_retry_and_quota_details():
    response = httpx.Response(429, headers={"Retry-After": "12", "X-Private": "private-secret"})
    error = APIError(429, {"error": {"message": "private text", "details": [
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "19.2s"},
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
         "violations": [{"quotaId": "RequestsPerDayPerProject", "subject": "private-project"}]},
    ]}}, response)
    wrapped = RuntimeError("private wrapper")
    wrapped.__cause__ = error
    assert error_metadata(wrapped) == {
        "upstream_status": 429, "retry_after_seconds": 20, "quota_scope": "daily",
    }
    assert retry_after_seconds("Wed, 01 Jan 2020 00:00:00 GMT") == 0


def test_pacing_does_not_retry_long_provider_wait_daily_limit_or_generic_503():
    pacing = CloudPacing(interval=1, backoff=1, max_wait=10)
    assert not pacing.retry({"status": 429, "response": {}, "retry_after_seconds": 11})
    assert not pacing.retry({"status": 429, "response": {"quota_scope": "daily"}})
    assert not pacing.retry({"status": 503, "response": {}})
    assert pacing.retries == 0


def test_pacing_respects_backoff_and_keeps_multiple_failed_attempts(monkeypatch):
    clock = [0]
    delays = []

    def sleep(seconds):
        delays.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr("evaluation.run.time.sleep", sleep)
    monkeypatch.setattr("evaluation.run.time.monotonic", lambda: clock[0])
    responses = iter([
        httpx.Response(429, headers={"Retry-After": "4"}, json={"detail": {"code": "rate_limit"}}),
        httpx.Response(503, json={"detail": {"code": "provider_error", "upstream_status": 503}}),
        httpx.Response(200, json={"answer": "Synthetic response"}),
    ])
    with httpx.Client(base_url="http://sandbox", transport=httpx.MockTransport(lambda _: next(responses))) as client:
        result = request_turn(client, "cloud", "Synthetic question", [], CloudPacing(interval=1, backoff=3, max_retries=2))
    assert result["status"] == 200
    assert delays == [4, 6]
    assert [a["status"] for a in result["prior_attempts"]] == [429, 503]


@pytest.mark.parametrize("pacing_update", [False, True])
def test_synthetic_checkpoint_dry_run_resumes_only_missing_and_failed_cases(tmp_path, monkeypatch, pacing_update):
    from pathlib import Path
    from evaluation import run

    source = Path(__file__).parent / "fixtures" / "gemini-partial.json"
    before = source.read_bytes()
    recorded = json.loads(before)
    backend = recorded["backend_container"]
    target = tmp_path / "resumed.json"
    monkeypatch.setenv("EVALUATION_ADMIN_TOKEN", "fake-local-test")
    monkeypatch.setattr(run, "verify_sandbox", lambda: "replacement" if pacing_update else backend)
    monkeypatch.setattr(run, "verify_transport_limit", lambda _: 10)
    monkeypatch.setattr(run, "verify_cloud_configuration", lambda _: None)
    monkeypatch.setattr(run, "verify_catalog", lambda *_: None)
    attempted = []

    def fake_case(client, case, provider, pacing):
        attempted.append(case["id"])
        return {"id": case["id"], "error": None, "response": {}}

    monkeypatch.setattr(run, "run_case", fake_case)
    monkeypatch.setattr("sys.argv", ["run", "--provider", "cloud", "--confirm-synthetic-sandbox",
        "--confirm-quota-budget", "--resume", str(source), "--output", str(target),
        "--smoke-report", str(source.with_name("smoke.json")),
        *(["--resume-after-pacing-update"] if pacing_update else [])])
    run.main()
    resumed = json.loads(target.read_text())
    assert attempted == ["P3-16", "P3-24", *[f"P3-{i}" for i in range(25, 45)]]
    assert len(resumed["runs"]) == 44
    assert len({item["id"] for item in resumed["runs"]}) == 44
    assert resumed["runs"][0] == recorded["runs"][0]
    assert resumed["case_progress"]["unattempted"] == []
    if pacing_update:
        assert resumed["backend_container"] == "replacement"
        assert resumed["resumed_from"][0]["backend_container"] == backend
        assert resumed["gemini_http_requests_per_minute"] == 10
        assert "transport_change" in resumed
    assert [item["id"] for item in resumed["prior_runs"]] == ["P3-16", "P3-24"]
    assert source.read_bytes() == before
