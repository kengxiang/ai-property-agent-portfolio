# Sandbox agent evaluation

The final evaluation ran the original 44 scenarios three times against the Docker Compose Sandbox using Ollama Qwen3.5:4B and BGE-M3. All three runs produced the same scores. Each run answered 44/44 scenarios correctly under the answer rubric and passed 43/44 under the strict rubric.

## Scope and method

The [fixed dataset](dataset.json) contains 24 synthetic property records, six chunks from bundled synthetic PDFs, and 44 scenarios covering search, details, comparisons, multi-turn context, RAG, missing evidence, and requests the read-only agent should refuse. The runner verifies the Sandbox backend and checks the catalog against the fixture before running. It replays reported search filters against the property API to score the returned property set.

The scorer uses exact checks where possible. Codex reviewed cases that required semantic assessment against the catalog or source text. These assessments were not independently human-reviewed. All three runs used the same benchmark expectations. Results describe this dataset and do not estimate real-world accuracy. Gemini was evaluated later on the same fixed dataset. See the [model comparison](MODEL_COMPARISON.md).

## Results

| Metric | Original baseline | Intermediate runs, each | Final runs, each |
|---|---:|---:|---:|
| HTTP success | 42/44 | 44/44 | 44/44 |
| Answer accuracy | 41/44 | 43/44 | 44/44 |
| Strict task success | 40/44 | 42/44 | 43/44 |
| Property precision | 22/22 | 28/29 | 28/28 |
| Property recall | 22/28 | 28/28 | 28/28 |
| Tool selection | 39/42 | 41/42 | 41/42 |
| Tool arguments | 41/44 | 42/44 | 43/44 |
| Valid returned citations | 12/12 | 12/12 | 12/12 |
| Expected citations in answer text | 8/8 | 8/8 | 8/8 |
| Appropriate no-answer handling | 6/7 | 7/7 | 7/7 |
| Reviewed cases with unsupported claims | 0/30 | 3/30 | 0/30 |

The final runs recorded mean successful-request latencies of 4.67, 4.68, and 4.67 seconds. Baseline mean latency was 4.98 seconds. Final P95 values were 8.12, 8.38, and 8.21 seconds, compared with 7.40 seconds at baseline. The runs did not control cold starts or caching. These are HTTP timings, not inference throughput. The P3 regression run recorded **223 passed, 3 skipped**. The later P5 suite recorded **247 passed, 3 skipped**.

## How to read the metrics

- **HTTP success** counts scenarios that complete without an HTTP or setup error, out of 44.
- **Answer accuracy** scores answer content separately from tools. It compares the presented property set or uses the scenario's semantic rubric.
- **Strict task success** also requires the expected tools and arguments, matching property results where applicable, and valid source metadata and answer citations where required.
- **Property precision and recall** count matching IDs from replayed structured searches across the search cases. Precision divides matches by all predicted IDs. Recall divides matches by the 28 expected IDs, counted across scenarios.
- **Tool selection** matches the complete set of calls on the 42 scenarios with expected tool use. **Tool argument accuracy** counts correctly matched calls out of 44 expected calls across those scenarios.
- **Returned citation validity** checks returned source metadata against the fixture and property scope on cases that score sources. **Citation coverage** measures whether the eight expected citations appear in the answer with the filename and page. These checks do not establish that every claim is supported.
- **No-answer handling** measures whether the assistant appropriately states what is unknown or refuses the unsupported request on seven designated cases.

## Remaining strict failure

In P3-25, Qwen correctly answered that P022 costs RM220,000 using the preceding verified turn. The original benchmark expects another details call, so all three runs lose one strict pass while getting the answer right. Recent evidence is appropriate for this immediate follow-up. A different property, missing evidence, or a request to refresh information would call for another lookup.

## Changes and reproduction

The backend validates tool arguments and permits one repair attempt. It enforces availability constraints and validates citation scope. Responses with detected unsupported transaction claims can fall back to verified facts and explicit uncertainty. These controls address the observed search, malformed call and unsupported claim cases. They do not provide general fact verification.

Use the [fresh Compose demo](../docs/DEMO.md), install the [Python dependencies](../requirements.txt) in a local virtual environment and run:

```bash
python -m evaluation.run --provider local --output evaluation/results/qwen-fresh.json
python -m evaluation.score evaluation/results/qwen-fresh.json --output evaluation/results/qwen-fresh-score.json
```

For a full semantic score, review answer accuracy, unsupported claims and citations against the fixtures and supply that case review with `--review`. Keep the fixed dataset and rubric unchanged. [Sanitized final aggregates](evidence/final-metrics.json)
