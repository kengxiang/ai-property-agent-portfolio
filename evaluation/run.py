"""Run the versioned benchmark through the Sandbox's existing read-only chat API."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import time

import httpx

from backend.schemas.property_filters import PropertyFilters
from evaluation.score import effective_filters
from evaluation.checkpoint import model_versions, record, resume_checkpoint, save
from llm.gemini_metrics import retry_after_seconds

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evaluation/dataset.json"
BASE_URL = "http://127.0.0.1:38000"
FROZEN_DIGEST = "139d1a29daf751d33ce12fc4180f850bcedf74fdab58713c708457ff0eba1049"
CLOUD_MODEL = "gemini-3.5-flash-lite"
SAFE_ERROR_CODES = {"rate_limit", "provider_timeout", "provider_error", "provider_configuration"}


class CloudPacing:
    """Pace chat starts with a shared retry budget across the invocation."""

    def __init__(self, interval=5, backoff=30, max_retries=1, max_wait=120):
        self.interval = interval
        self.backoff = backoff
        self.max_retries = max_retries
        self.max_wait = max_wait
        self.next_turn = 0.0
        self.retries = 0

    def wait(self):
        remaining = self.next_turn - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        self.next_turn = time.monotonic() + self.interval

    def retry(self, result):
        metadata = result["response"]
        if metadata.get("quota_scope") in {"daily", "billing"}:
            return False
        if result["status"] != 429 and metadata.get("upstream_status") != 503:
            return False
        if self.retries >= self.max_retries:
            return False
        delay = max(self.backoff * 2 ** self.retries, result.get("retry_after_seconds", 0))
        if delay > self.max_wait:
            return False
        self.retries += 1
        self.next_turn = max(self.next_turn, time.monotonic() + delay)
        return True


def now():
    return datetime.now(timezone.utc).isoformat()


def filters_query(filters):
    return filters.model_dump(mode="json", exclude_defaults=True)


def property_ids(client, filters):
    response = client.get("/properties", params=filters_query(filters))
    response.raise_for_status()
    return sorted(row["property_id"] for row in response.json())


def verify_catalog(client, dataset):
    for entry in dataset["catalog"]:
        response = client.get(f"/properties/{entry['property_id']}")
        response.raise_for_status()
        if response.json() != entry:
            raise RuntimeError(f"Catalog drift for {entry['property_id']}")
    for case in dataset["cases"]:
        if case["property_ids"] is not None:
            if property_ids(client, PropertyFilters(**case["ground_truth"]["filters"])) != case["property_ids"]:
                raise RuntimeError(f"Catalog search drift for {case['id']}")


def _request_turn(client, provider, question, history):
    start = time.perf_counter()
    try:
        response = client.post("/agent/chat", json={"provider": provider, "message": question, "history": history, "cloud_consent": provider == "cloud"})
        result = response.json() if response.is_success else {}
        hint = None
        if provider == "cloud" and response.status_code in (429, 503, 504):
            hint = retry_after_seconds(response.headers.get("Retry-After"))
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = None
            if isinstance(detail, dict) and detail.get("code") in SAFE_ERROR_CODES:
                result["provider_error"] = detail["code"]
                result["provider_usage"] = detail.get("provider_usage")
                if type(detail.get("gemini_http_requests")) is int:
                    result["gemini_http_requests"] = detail["gemini_http_requests"]
                if type(detail.get("upstream_status")) is int:
                    result["upstream_status"] = detail["upstream_status"]
                if detail.get("quota_scope") in ("daily", "billing", "requests_per_minute", "tokens_per_minute"):
                    result["quota_scope"] = detail["quota_scope"]
                if type(detail.get("retry_after_seconds")) is int and detail["retry_after_seconds"] >= 0:
                    hint = max(hint or 0, detail["retry_after_seconds"])
        return {
            "question": question, "status": response.status_code,
            "client_ms": round((time.perf_counter() - start) * 1000, 2), "response": result,
            "error": None if response.is_success else f"HTTP {response.status_code}",
            **({"retry_after_seconds": hint} if hint is not None else {}),
        }
    except (httpx.RequestError, ValueError) as error:
        return {"question": question, "status": None, "client_ms": round((time.perf_counter() - start) * 1000, 2),
                "response": {}, "error": type(error).__name__}


def request_turn(client, provider, question, history, pacing=None):
    attempts = []
    while True:
        if pacing:
            pacing.wait()
        result = _request_turn(client, provider, question, history)
        if not pacing or not pacing.retry(result):
            if attempts:
                result["previous_attempt"] = attempts[-1]
                if len(attempts) > 1:
                    result["prior_attempts"] = attempts
            return result
        attempts.append(result)


def run_case(client, case, provider, pacing=None):
    history, setup = [], []
    for question in case["prior"]:
        turn = request_turn(client, provider, question, history, pacing)
        setup.append(turn)
        if turn["error"] is not None:
            return {"id": case["id"], "setup": setup, "error": "Setup failed", "response": {}}
        history.append({"question": question[:4000], "answer": turn["response"]["answer"][:4000]})
    run = request_turn(client, provider, case["question"], history, pacing)
    run.update(id=case["id"], setup=setup)
    lookup = []
    for call in run["response"].get("tool_calls", []):
        if call["name"] == "search_properties":
            try:
                lookup.extend(property_ids(client, PropertyFilters(**call["args"])))
            except (httpx.HTTPError, TypeError, ValueError):
                run["search_replay_failed"] = True
    run["replayed_search_ids"] = sorted(set(lookup))
    return run


def verify_sandbox():
    ids = subprocess.check_output([
        "docker", "compose", "--env-file", ".env.compose", "-p", "ai-property-portfolio",
        "ps", "-q", "backend",
    ], cwd=ROOT, text=True).split()
    if len(ids) != 1:
        raise RuntimeError("Expected one running Sandbox backend")
    port = subprocess.check_output([
        "docker", "port", ids[0], "8000/tcp",
    ], text=True).strip()
    if port != "127.0.0.1:38000":
        raise RuntimeError("Expected the Sandbox backend on 127.0.0.1:38000")
    postgres_id = subprocess.check_output([
        "docker", "compose", "--env-file", ".env.compose", "-p", "ai-property-portfolio",
        "ps", "-q", "postgres",
    ], cwd=ROOT, text=True).strip()
    if not postgres_id:
        raise RuntimeError("Expected a running Sandbox PostgreSQL container")
    volume = subprocess.check_output([
        "docker", "inspect", postgres_id, "--format",
        '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}',
    ], text=True).strip()
    if volume != "ai-property-portfolio_postgres_data":
        raise RuntimeError("Expected the dedicated Sandbox PostgreSQL volume")
    return ids[0]


def verify_cloud_configuration(backend_id):
    # The container emits only a Boolean. No credentials enter benchmark output.
    result = subprocess.check_output([
        "docker", "exec", backend_id, "python", "-c",
        "import os,sys; "
        "print(bool(os.getenv('GEMINI_API_KEY','').strip()) and "
        "os.getenv('GEMINI_MODEL') == sys.argv[1])", CLOUD_MODEL,
    ], text=True).strip()
    if result != "True":
        raise RuntimeError(f"Sandbox requires a Gemini key and GEMINI_MODEL={CLOUD_MODEL}")


SMOKE_CASES = (("BASIC", "Say hello in one sentence."),)


def smoke_cases(dataset):
    ids = ("P3-10", "P3-17", "P3-31", "P3-39")
    cases = {item["id"]: item for item in dataset["cases"]}
    return [{"id": name, "question": question, "prior": []} for name, question in SMOKE_CASES] + [
        cases[case_id] for case_id in ids
    ]


def smoke_checks(runs):
    results = {run["id"]: run for run in runs if not run["error"]}

    def response(case):
        return results.get(case, {}).get("response", {})

    listing = results.get("P3-10", {})
    calls = response("P3-10").get("tool_calls", [])
    expected = PropertyFilters(location="KL", property_type="CONDO",
                               min_price=540000, max_price=570000, near_mrt=True)
    arguments_match = False
    if len(calls) == 1 and calls[0]["name"] == "search_properties":
        try:
            arguments_match = effective_filters(calls[0]["args"]) == expected
        except (TypeError, ValueError):
            pass
    detail = response("P3-17")
    rag = response("P3-31")
    missing = response("P3-39")
    checks = {
        "basic": bool(response("BASIC").get("answer")),
        "property_search": arguments_match and listing.get("replayed_search_ids") == ["P001"]
                           and "P001" in response("P3-10").get("answer", ""),
        "property_details": bool(re.search(r"(?<![0-9])220[ ,]?000(?![0-9])", detail.get("answer", "")))
                            and any(c["name"] == "get_property_details" and c["args"].get("property_id") == "P022"
                                    for c in detail.get("tool_calls", [])),
        "rag_citation": "written" in rag.get("answer", "").lower()
                        and bool(re.search(r"P001_house_rules\.pdf.{0,80}\b(?:page|p\.)[\s*:]*1\b",
                                           rag.get("answer", ""), re.IGNORECASE | re.DOTALL))
                        and any(s["document_id"] == "DOC002" and s["property_id"] == "P001" and s["page"] == 1
                                for s in rag.get("sources", []))
                        and all(s["property_id"] == "P001" for s in rag.get("sources", [])),
        "missing_evidence": not missing.get("sources") and ".pdf" not in missing.get("answer", "").lower() and any(
            phrase in missing.get("answer", "").lower()
            for phrase in ("cannot confirm", "unable to confirm", "no supporting", "cannot verify")
        ),
    }
    return checks


def stop_reason(run):
    failed = [run, *run.get("setup", [])]
    if any(turn.get("status") in (401, 403, 422, 429)
           or turn.get("response", {}).get("provider_error") == "provider_configuration"
           or turn.get("response", {}).get("quota_scope") in ("daily", "billing") for turn in failed):
        return "authorization, input, or quota check failed; inspect the saved run before continuing"
    return None


def verify_transport_limit(backend_id):
    configured = subprocess.check_output([
        "docker", "exec", backend_id, "python", "-c",
        "from llm.gemini_rate_limit import shared_limiter; print(shared_limiter().limit)",
    ], text=True).strip()
    if not configured.isdecimal() or not 1 <= int(configured) <= 10:
        raise RuntimeError("Expected the Sandbox Gemini HTTP limiter at 10 RPM or lower")
    return int(configured)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["local", "cloud"], default="local")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, help="Environment metadata JSON")
    parser.add_argument("--smoke", action="store_true", help="Run five synthetic smoke requests only")
    parser.add_argument("--confirm-synthetic-sandbox", action="store_true",
                        help="Confirm that Sandbox catalog and documents were reviewed as synthetic")
    parser.add_argument("--smoke-report", type=Path, help="Passing cloud smoke JSON from this Sandbox backend")
    parser.add_argument("--resume", type=Path, help="Existing cloud checkpoint; writes a separate output")
    parser.add_argument("--resume-after-pacing-update", action="store_true",
                        help="Attest that a replaced Sandbox backend differs only in Gemini transport and diagnostics")
    parser.add_argument("--confirm-quota-budget", action="store_true",
                        help="Confirm the Google project's quota and approved budget were checked")
    parser.add_argument("--pace-seconds", type=int, choices=range(1, 301), metavar="1..300", default=5)
    parser.add_argument("--retry-budget", type=int, choices=range(4), default=1)
    parser.add_argument("--backoff-seconds", type=int, choices=range(1, 121), metavar="1..120", default=30)
    parser.add_argument("--max-cases", type=int, choices=range(1, 45), metavar="1..44", default=44)
    args = parser.parse_args()
    if args.resume_after_pacing_update and not args.resume:
        parser.error("--resume-after-pacing-update requires --resume")
    if args.resume and (args.provider != "cloud" or args.smoke or args.max_cases != 44 or args.metadata):
        parser.error("--resume requires a full cloud benchmark and preserves the original metadata")
    if args.output.exists():
        parser.error("Output already exists; choose a new filename")
    if args.provider == "cloud" and not args.confirm_quota_budget:
        parser.error("Check the Google project quota and budget before passing --confirm-quota-budget")
    token = os.getenv("EVALUATION_ADMIN_TOKEN")
    if args.provider == "cloud" and not token:
        parser.error("Cloud evaluation requires an administrator session in EVALUATION_ADMIN_TOKEN")
    if args.provider == "cloud" and not args.confirm_synthetic_sandbox:
        parser.error("Review all Sandbox tool data and pass --confirm-synthetic-sandbox before sending it to Gemini")
    dataset_bytes = DATASET.read_bytes()
    digest = hashlib.sha256(dataset_bytes).hexdigest()
    if args.provider == "cloud" and digest != FROZEN_DIGEST:
        parser.error("Cloud evaluation requires the reviewed, frozen synthetic dataset")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    backend_id = verify_sandbox()
    if args.provider == "cloud":
        verify_cloud_configuration(backend_id)
        if args.resume_after_pacing_update:
            transport_rate = verify_transport_limit(backend_id)
            source = json.loads(args.resume.read_text())
            previous_backends = {source.get("backend_container"), *(
                item.get("backend_container") for item in source.get("resumed_from", [])
            )} - {None}
        else:
            previous_backends = set()
        if not args.smoke:
            if not args.smoke_report:
                parser.error("Pass --smoke-report after running and reviewing the cloud smoke test")
            smoke = json.loads(args.smoke_report.read_text())
            checks = smoke_checks(smoke.get("runs", []))
            if ((smoke.get("provider"), smoke.get("mode"), smoke.get("dataset_sha256")) != ("cloud", "smoke", digest)
                or smoke.get("backend_container") not in ({backend_id} | previous_backends)
                or not all(checks.values())):
                parser.error("Expected a passing smoke report from the same Sandbox backend")
    dataset = json.loads(dataset_bytes)
    cases = smoke_cases(dataset) if args.smoke else dataset["cases"][:args.max_cases]
    pacing = CloudPacing(args.pace_seconds, args.backoff_seconds, args.retry_budget) if args.provider == "cloud" else None
    metadata = json.loads(args.metadata.read_text()) if args.metadata else {}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "provider": args.provider, "started_at": now(), "backend_container": backend_id,
        "dataset_sha256": digest, "metadata": metadata, "runs": [],
        "planned_cases": len(cases), "mode": "smoke" if args.smoke else "benchmark",
    }
    case_ids = [case["id"] for case in cases]
    if args.resume:
        try:
            result.update(resume_checkpoint(args.resume, case_ids, backend_id, digest,
                                            pacing_update=args.resume_after_pacing_update))
            if args.resume_after_pacing_update:
                result["gemini_http_requests_per_minute"] = transport_rate
        except (ValueError, KeyError, OSError) as error:
            parser.error(str(error))
    inherited = {row["id"] for row in result["runs"] if row["error"] is None}
    inherited_versions = model_versions(result["runs"]) if args.resume else set()
    if pacing:
        result["pacing"] = {"interval_seconds": args.pace_seconds, "backoff_seconds": args.backoff_seconds,
                            "retry_budget": args.retry_budget, "maximum_wait_seconds": pacing.max_wait}
    with args.output.open("x", encoding="utf-8") as destination:
        os.fchmod(destination.fileno(), 0o600)
    save(result, case_ids, args.output)
    with httpx.Client(base_url=BASE_URL, trust_env=False, timeout=90, headers=headers) as client:
        verify_catalog(client, dataset)
        for case in cases:
            if case["id"] in inherited:
                continue
            run = run_case(client, case, args.provider, pacing)
            versions = model_versions([run])
            if inherited_versions and versions - inherited_versions:
                result.setdefault("configuration_mismatch_runs", []).append(run)
                result["stopped_reason"] = "Gemini returned a different model version; result kept separately"
                break
            record(result, run, case_ids)
            save(result, case_ids, args.output)
            print(case["id"], run.get("client_ms"), run["error"] or "OK", flush=True)
            if pacing and (reason := stop_reason(run)):
                result["stopped_reason"] = reason
                break
    if args.smoke:
        result["smoke_checks"] = smoke_checks(result["runs"])
        if not all(result["smoke_checks"].values()):
            result.setdefault("stopped_reason", "Smoke checks did not all pass; review the saved responses")
    result["finished_at"] = now()
    save(result, case_ids, args.output)
    progress = result["case_progress"]
    print(f"Successful: {len(progress['successful'])}/{len(case_ids)}; "
          f"failed: {len(progress['failed'])}; unattempted: {len(progress['unattempted'])}")
    if result.get("stopped_reason"):
        raise SystemExit(result["stopped_reason"])


if __name__ == "__main__":
    main()
