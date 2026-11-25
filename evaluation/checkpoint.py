"""Keep one current outcome per case and retain superseded failures."""

import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def resume_checkpoint(source, case_ids, backend_id, digest, *, pacing_update=False):
    data = source.read_bytes()
    checkpoint = json.loads(data)
    source_backend = checkpoint.get("backend_container")
    expected_backend = source_backend if pacing_update and source_backend else backend_id
    expected = ("cloud", "benchmark", digest, expected_backend, len(case_ids))
    actual = tuple(checkpoint.get(key) for key in (
        "provider", "mode", "dataset_sha256", "backend_container", "planned_cases",
    ))
    if actual != expected:
        raise ValueError("Resume requires the same dataset, provider, full case plan and Sandbox backend container")
    runs = checkpoint["runs"]
    ids = [run["id"] for run in runs]
    if len(set(ids)) != len(ids) or not set(ids) <= set(case_ids):
        raise ValueError("Checkpoint contains duplicate or unknown case IDs")
    if checkpoint.get("case_progress", {}).get("planned", list(case_ids)) != list(case_ids):
        raise ValueError("Checkpoint case order or plan changed")
    if len(ids) == len(case_ids) and all(run["error"] is None for run in runs):
        raise ValueError("Checkpoint already has successful results for every case")
    lineage = checkpoint.get("resumed_from", []) + [{
        "sha256": hashlib.sha256(data).hexdigest(),
        "backend_container": source_backend,
        "started_at": checkpoint["started_at"],
        "finished_at": checkpoint.get("finished_at"),
        "stopped_reason": checkpoint.get("stopped_reason"),
        "pacing": checkpoint.get("pacing"),
    }]
    transport_change = checkpoint.get("transport_change")
    if pacing_update and source_backend != backend_id:
        transport_change = "Gemini HTTP pacing update; model, tools and data confirmed unchanged"
    return {
        **({"transport_change": transport_change} if transport_change else {}),
        "metadata": checkpoint.get("metadata", {}), "runs": runs,
        "prior_runs": checkpoint.get("prior_runs", []), "resumed_from": lineage,
    }


def model_versions(runs):
    return {
        version
        for run in runs
        for turn in [*run.get("setup", []), run]
        for version in (turn.get("response", {}).get("provider_usage") or {}).get("model_versions", [])
    }


def record(result, run, case_ids):
    previous = {row["id"]: row for row in result["runs"]}
    case_id = run["id"]
    if case_id in previous:
        if previous[case_id]["error"] is None:
            raise ValueError("Cannot replace a completed case")
        result.setdefault("prior_runs", []).append(previous[case_id])
    previous[case_id] = run
    result["runs"] = [previous[id_] for id_ in case_ids if id_ in previous]


def save(result, case_ids, path):
    completed = {run["id"]: run["error"] is None for run in result["runs"]}
    result["case_progress"] = {
        "planned": list(case_ids),
        "successful": [id_ for id_ in case_ids if completed.get(id_) is True],
        "failed": [id_ for id_ in case_ids if completed.get(id_) is False],
        "unattempted": [id_ for id_ in case_ids if id_ not in completed],
    }
    temp = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temp = Path(handle.name)
            json.dump(result, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
