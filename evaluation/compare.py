"""Compare repeated runs with the frozen benchmark, keeping answer and tool scores separate."""

import argparse
import hashlib
import json
from pathlib import Path

from evaluation.score import ratio, score


def comparison(dataset, run, review):
    cases = {case["id"]: case for case in dataset["cases"]}
    scored = score(dataset, run, review)
    answer_passes, answer_decided = 0, 0
    answer_failures = []
    for row in scored["cases"]:
        expected_ids = cases[row["id"]]["property_ids"]
        if row["error"]:
            passed = False
        elif row["human_required"]:
            passed = (row["human_review"] or {}).get("passes")
        elif expected_ids is not None:
            passed = set(row["answer_ids"]) == set(expected_ids)
        else:
            passed = None
        if passed is not None:
            answer_decided += 1
            answer_passes += passed
            if not passed:
                answer_failures.append(row["id"])
    return {
        "started_at": run["started_at"], "finished_at": run.get("finished_at"),
        "overall": scored["overall"],
        "answer_task_success": ratio(answer_passes, answer_decided),
        "answer_task_pending": len(scored["cases"]) - answer_decided,
        "answer_task_failures": answer_failures,
        "strict_failures": [row["id"] for row in scored["cases"] if row["task"] is False],
        "target_cases": {
            row["id"]: {key: row[key] for key in ("error", "tool", "task", "no_answer")}
            for row in scored["cases"] if row["id"] in {"P3-01", "P3-02", "P3-25", "P3-44"}
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("evaluation/dataset.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("pairs", nargs="+", help="Pairs: RUN.json REVIEW.json, repeated")
    args = parser.parse_args()
    if len(args.pairs) % 2:
        parser.error("Provide a review file for each run")
    dataset_content = args.dataset.read_bytes()
    dataset = json.loads(dataset_content)
    digest = hashlib.sha256(dataset_content).hexdigest()
    comparisons = {}
    for index in range(0, len(args.pairs), 2):
        run_path, review_path = map(Path, args.pairs[index:index + 2])
        run = json.loads(run_path.read_text())
        if run["dataset_sha256"] != digest or set(case["id"] for case in dataset["cases"]) != {
            item["id"] for item in run["runs"]
        } or not run.get("finished_at"):
            parser.error(f"{run_path} is incomplete or used a different dataset")
        comparisons[run_path.stem] = comparison(dataset, run, json.loads(review_path.read_text()))
    args.output.write_text(json.dumps(comparisons, indent=2) + "\n")


if __name__ == "__main__":
    main()
