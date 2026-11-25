import json
from pathlib import Path

from evaluation.score import args_match, assess, latency, ratio, score, source_key


DATASET = json.loads((Path(__file__).resolve().parents[1] / "evaluation/dataset.json").read_text())


def test_dataset_is_versioned_and_has_ground_truth():
    cases = DATASET["cases"]
    assert 40 <= len(cases) <= 50
    assert len(cases) == len({case["id"] for case in cases})
    docs = {source_key(doc) for doc in DATASET["document_chunks"]}
    for case in cases:
        assert case["question"] and case["ground_truth"] and case["scoring"]
        assert all(source_key(doc) in docs for doc in case["sources"] or [])


def test_normalized_filter_equivalence_and_exact_effective_parameters():
    expected = {"name": "search_properties", "args": {"location": "KUALA_LUMPUR", "max_price": 600000}}
    actual = {"name": "search_properties", "args": {"location": "KL", "max_price": 600000, "near_mrt": False}}
    assert args_match(expected, actual)
    actual["args"]["near_mrt"] = True
    assert not args_match(expected, actual)


def test_wrong_property_source_is_invalid_despite_existing_pdf():
    case = next(case for case in DATASET["cases"] if case["id"] == "P3-31")
    cross_source = next(chunk for chunk in DATASET["document_chunks"] if chunk["property_id"] == "P002")
    run = {
        "error": None, "response": {"answer": "Pets not allowed", "tool_calls": case["tools"], "sources": [cross_source]},
    }
    result = assess(case, run, {source_key(cross_source)}, None)
    assert result["sources"] == [0, 1]
    assert result["retrieval"] == [0, 1]
    assert result["task"] is False


def test_metrics_have_defined_denominators_and_latency():
    cases = DATASET["cases"][:2]
    first = cases[0]
    runs = [
        {"id": first["id"], "error": None, "client_ms": 100,
         "replayed_search_ids": first["property_ids"],
         "response": {"answer": ", ".join(first["property_ids"]), "tool_calls": first["tools"], "sources": []}},
        {"id": cases[1]["id"], "error": "HTTP 503", "client_ms": 999, "response": {}},
    ]
    summary = score(DATASET, {"runs": runs}, {})["overall"]
    assert summary["error_rate"] == ratio(1, 2)
    assert summary["task_success"] == ratio(1, 2)
    assert summary["tool_arguments"] == ratio(1, 2)
    assert summary["property_precision"] == ratio(len(first["property_ids"]), len(first["property_ids"]))
    assert summary["property_recall"]["denominator"] == sum(len(case["property_ids"]) for case in cases)
    assert summary["client_latency_ms"] == latency([100])
    assert summary["retrieval_hit"]["value"] is None


def test_answer_metric_does_not_require_a_fresh_tool_for_a_fact_in_history():
    from evaluation.compare import comparison

    case = next(case for case in DATASET["cases"] if case["id"] == "P3-25")
    run = {
        "started_at": "example",
        "runs": [{"id": case["id"], "error": None,
                  "response": {"answer": "P022 costs RM220,000.", "tool_calls": []}}],
    }
    report = comparison(DATASET, run, {case["id"]: {"passes": True}})
    assert report["answer_task_success"] == ratio(1, 1)
    assert report["overall"]["tool_selection"] == ratio(0, 1)
    assert report["overall"]["task_success"] == ratio(0, 1)
