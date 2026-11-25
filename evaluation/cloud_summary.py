"""Summarize numeric Gemini usage, errors and citation scope independently of scoring."""

import argparse
from collections import Counter
import json
from pathlib import Path

from evaluation.run import CLOUD_MODEL, FROZEN_DIGEST


def summarize(run, dataset):
    case_map = {case["id"]: case for case in dataset["cases"]}
    errors = Counter()
    violations, model_versions, tool_errors = [], set(), []
    input_tokens = output_tokens = calls = observed = turns = 0
    missing_usage = 0
    http_requests = http_counted_attempts = 0
    for case in [*run.get("prior_runs", []), *run["runs"], *run.get("configuration_mismatch_runs", [])]:
        expected = case_map.get(case["id"], {}).get("ground_truth", {}).get("property_id")
        response = case["response"]
        if expected and any(s["property_id"] != expected for s in response.get("sources", [])):
            violations.append(case["id"])
        if any(t.get("response", {}).get("tool_argument_failures", 0) for t in [*case.get("setup", []), case]):
            tool_errors.append(case["id"])
        for turn in [*case.get("setup", []), case]:
            if "status" not in turn:
                continue
            history = turn.get("prior_attempts", [turn["previous_attempt"]] if "previous_attempt" in turn else [])
            for attempt in [*history, turn]:
                turns += 1
                body = attempt["response"]
                if attempt["error"]:
                    errors[body.get("provider_error") or attempt["error"]] += 1
                if type(body.get("gemini_http_requests")) is int:
                    http_requests += body["gemini_http_requests"]
                    http_counted_attempts += 1
                usage = body.get("provider_usage") or {}
                calls += usage.get("calls", 0)
                observed += usage.get("calls_with_usage", 0)
                model_versions.update(usage.get("model_versions", []))
                if not usage.get("calls") or usage["calls"] != usage["calls_with_usage"]:
                    missing_usage += 1
                input_tokens += usage.get("input_tokens") or 0
                output_tokens += usage.get("output_tokens") or 0
    estimate = (input_tokens * 0.30 + output_tokens * 2.50) / 1_000_000 if observed else None
    return {
        "benchmark_cases_recorded": len(run["runs"]), "attempted_chat_turns_including_retries": turns,
        "errors_by_safe_code": dict(sorted(errors.items())), "wrong_property_citation_cases": violations,
        "tool_argument_failure_cases": tool_errors,
        "model_calls_observed": calls, "model_calls_with_usage": observed,
        "chat_attempts_without_complete_usage": missing_usage, "reported_model_versions": sorted(model_versions),
        "reported_input_tokens": input_tokens if observed else None,
        "reported_output_tokens_including_thinking": output_tokens if observed else None,
        "estimated_usd_for_reported_tokens_at_standard_paid_rates": estimate,
        "pricing_model": CLOUD_MODEL,
        **({"measured_gemini_http_requests": http_requests,
            "chat_attempts_with_http_request_count": http_counted_attempts} if http_counted_attempts else {}),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    run = json.loads(args.run.read_text())
    if run.get("provider") != "cloud" or run.get("dataset_sha256") != FROZEN_DIGEST:
        parser.error("Expected a Gemini run of the frozen synthetic dataset")
    dataset = json.loads(Path("evaluation/dataset.json").read_text())
    print(json.dumps(summarize(run, dataset), indent=2))


if __name__ == "__main__":
    main()
