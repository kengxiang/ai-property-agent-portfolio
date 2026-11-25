"""Score the frozen benchmark separately from semantic review."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from statistics import mean, median, quantiles

from backend.schemas.property_filters import PropertyFilters


ID_PATTERN = re.compile(r"\b(?:P206C?-[0-9A-F]{8}|P\d{3})\b", re.IGNORECASE)
READ_TOOLS = {"search_properties", "get_property_details", "search_documents"}


def source_key(source):
    return tuple(source.get(field) for field in ("property_id", "document_id", "filename", "page"))


def effective_filters(args):
    filters = PropertyFilters(**args)
    if filters.max_mrt_distance_m is not None:
        filters.near_mrt = False  # Explicit distance takes precedence in both repositories.
    return filters


def args_match(expected, actual):
    if actual["name"] != expected["name"]:
        return False
    try:
        if expected["name"] == "search_properties":
            return effective_filters(expected["args"]) == effective_filters(actual["args"])
        return all(
            str(actual["args"].get(key, "")).strip().upper() == str(value).upper()
            for key, value in expected["args"].items()
        )
    except (ValueError, TypeError):
        return False


def assess(case, run, known_sources, review, catalog_names=None):
    result = run["response"]
    calls = result.get("tool_calls", [])
    expected = case["tools"]
    answer = result.get("answer", "")
    expected_ids = set(case["property_ids"] or [])
    predicted = set(run.get("replayed_search_ids", []))
    mentioned = {value.upper() for value in ID_PATTERN.findall(answer)}
    if case["property_ids"] is not None:
        folded = answer.casefold()
        mentioned.update(
            property_id for property_id, name in (catalog_names or {}).items()
            if re.search(rf"(?<!\w){re.escape(name.casefold())}(?!\w)", folded)
        )
    used = Counter(tool["name"] for tool in calls)
    required = Counter(tool["name"] for tool in expected)
    tool_ok = used == required if expected else None
    remaining = list(calls)
    matched = 0
    for tool in expected:
        for number, actual in enumerate(remaining):
            if args_match(tool, actual):
                matched += 1
                remaining.pop(number)
                break

    sources = {source_key(source) for source in result.get("sources", [])}
    relevant = {source_key(source) for source in case["sources"] or []}
    expected_scope = case["ground_truth"].get("property_id")
    correct_sources = {source for source in sources & known_sources if not expected_scope or (
        case["sources"] is None or source[0] == expected_scope
    )}
    asked_for_citation = bool(relevant)
    explicit = {
        source for source in relevant
        if re.search(
            rf"{re.escape(source[2])}[\s\S]{{0,60}}?\b(?:page|p\.)[\s*:]*{source[3]}\b",
            answer, re.IGNORECASE,
        )
    }
    objective = not run["error"] and bool(answer) and set(used) <= READ_TOOLS
    if expected:
        objective = objective and tool_ok and matched == len(expected)
    if case["property_ids"] is not None:
        objective = objective and predicted == expected_ids and mentioned == expected_ids and not run.get("search_replay_failed")
    if case["sources"] is not None:
        objective = objective and sources == correct_sources
    if asked_for_citation:
        objective = objective and relevant <= sources and explicit == relevant
    human = review.get("passes") if review is not None else None
    task = None if objective and case["scoring"]["human_review"] and human is None else (
        bool(objective) and human is not False
    )
    return {
        "id": case["id"], "category": case["category"], "error": run["error"],
        "tool": tool_ok, "arguments": [matched, len(expected)], "task": task, "objective": bool(objective),
        "property": [len(predicted & expected_ids), len(predicted), len(expected_ids)]
        if case["property_ids"] is not None else None,
        "replayed_ids": sorted(predicted), "answer_ids": sorted(mentioned),
        "sources": [len(correct_sources), len(sources)] if case["sources"] is not None else None,
        "retrieval": [len(relevant & sources), len(relevant)] if relevant else None,
        "answer_citation": [len(explicit), len(relevant)] if relevant else None,
        "human_review": review,
        "human_required": bool(case["scoring"]["human_review"]),
        "no_answer": review.get("no_answer") if review and case["scoring"]["no_answer"] else None,
        "rag_grounded": review.get("grounded") if review and case["sources"] is not None else None,
        "latency_ms": run.get("client_ms") if not run["error"] else None,
        "api_ms": result.get("latency_ms"),
    }


def ratio(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "value": round(numerator / denominator, 4) if denominator else None}


def latency(values):
    if not values:
        return None
    return {"sample_size": len(values), "mean": round(mean(values), 2), "p50": round(median(values), 2),
            "p95": round(quantiles(values, n=100, method="inclusive")[94] if len(values) > 1 else values[0], 2)}


def summarize(rows):
    def rates(values):
        scored = [value for value in values if value is not None]
        return ratio(sum(scored), len(scored))

    def micro(key):
        values = [row[key] for row in rows if row[key] is not None]
        return ratio(sum(value[0] for value in values), sum(value[1] for value in values))

    property_rows = [row["property"] for row in rows if row["property"] is not None]
    review_rows = [row["human_review"] for row in rows if row["human_review"] is not None]
    return {
        "cases": len(rows), "error_rate": rates(bool(row["error"]) for row in rows),
        "objective_pass": rates(row["objective"] for row in rows),
        "task_success": rates(row["task"] for row in rows),
        "pending_semantic_reviews": sum(row["human_required"] and row["human_review"] is None for row in rows),
        "tool_selection": rates(row["tool"] for row in rows),
        "tool_arguments": micro("arguments"),
        "property_precision": ratio(sum(p[0] for p in property_rows), sum(p[1] for p in property_rows)),
        "property_recall": ratio(sum(p[0] for p in property_rows), sum(p[2] for p in property_rows)),
        "retrieval_hit": rates(row["retrieval"][0] > 0 for row in rows if row["retrieval"] is not None),
        "retrieval_recall": micro("retrieval"),
        "returned_source_validity": micro("sources"),
        "answer_citation_coverage": micro("answer_citation"),
        "rag_grounding_reviewed": rates(row["rag_grounded"] for row in rows),
        "no_answer_reviewed": rates(row["no_answer"] for row in rows),
        "unsupported_claims_reviewed": rates(review.get("unsupported_claim") for review in review_rows),
        "client_latency_ms": latency([row["latency_ms"] for row in rows if row["latency_ms"] is not None]),
        "api_latency_ms": latency([row["api_ms"] for row in rows if row["api_ms"] is not None]),
    }


def score(dataset, benchmark, review):
    cases = {case["id"]: case for case in dataset["cases"]}
    sources = {source_key(chunk) for chunk in dataset["document_chunks"]}
    runs = benchmark["runs"]
    if len({run["id"] for run in runs}) != len(runs) or not all(run["id"] in cases for run in runs):
        raise ValueError("Duplicate or unknown case ID")
    names = {row["property_id"]: row["name"] for row in dataset["catalog"]}
    rows = [assess(cases[run["id"]], run, sources, review.get(run["id"]), names) for run in runs]
    return {"overall": summarize(rows), "by_category": {
        category: summarize([row for row in rows if row["category"] == category])
        for category in sorted({row["category"] for row in rows})
    }, "cases": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--dataset", type=Path, default=Path("evaluation/dataset.json"))
    parser.add_argument("--review", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw_dataset = args.dataset.read_bytes()
    run = json.loads(args.run.read_text())
    if hashlib.sha256(raw_dataset).hexdigest() != run["dataset_sha256"]:
        parser.error("The run was produced with a different dataset")
    review = json.loads(args.review.read_text()) if args.review else {}
    summary = score(json.loads(raw_dataset), run, review)
    args.output.write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()
