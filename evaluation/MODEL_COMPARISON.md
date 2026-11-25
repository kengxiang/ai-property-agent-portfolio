# Qwen and Gemini evaluation

Both providers were evaluated with the same [44 fixed synthetic cases](dataset.json), LangGraph tools, PostgreSQL/pgvector catalog and BGE-M3 retriever. Qwen3.5:4B ran locally in three complete rounds. Gemini 3.5 Flash-Lite ran through the Gemini Developer API in two stages. The final Gemini metrics combine 22 preserved successes with 22 completed or retried cases. This is a resumed evaluation.

## Model and transport checks

Five Gemini smoke checks passed, covering basic chat, filtered search, P022 details, a P001 citation and an appropriate no-answer response. In the initial 24 cases, P3-09 recovered from a 429 after a runner-imposed delay. P3-16 returned an application HTTP 503 with an unknown upstream cause. P3-24 returned HTTP 429 and stopped the run. Dashboard evidence showed a peak of 19 requests per minute against a 15 RPM project limit. The archived responses lack enough quota metadata to attribute each 429 individually.

The continuation used backend HTTP admission control at 10 requests per rolling minute. The limiter covers tool continuations and retries. The checkpoint preserved the earlier attempts and retained the original scoring criteria.

## Final measured outcomes

| Metric | Qwen final runs, each | Gemini resumed result |
|---|---:|---:|
| Answer accuracy | 44/44 | 44/44 |
| Strict task success | 43/44 | 41/44 |
| HTTP success for final recorded outcomes | 44/44 | 44/44 |
| Tool selection | 41/42 | 39/42 |
| Tool arguments | 43/44 | 41/44 |
| Property precision / recall | 28/28 · 28/28 | 28/28 · 28/28 |
| Returned citation validity / explicit citation coverage | 12/12 · 8/8 | 12/12 · 8/8 |
| RAG retrieval hits | 8/8 | 8/8 |
| No-answer handling | 7/7 | 7/7 |
| Cases with unsupported claims among reviewed cases | 0/30 | 0/30 |
| Mean successful HTTP latency (s) | 4.67 / 4.68 / 4.67 | 7.52 |
| Median successful HTTP latency (s) | 3.95 / 3.81 / 3.76 | 7.66 |
| P95 successful HTTP latency (s) | 8.12 / 8.38 / 8.21 | 14.44 |

P3-25, P3-26 and P3-27 answered correctly from prior context. Gemini omitted the fresh tool call required by the strict rubric in each case. The resumed Gemini latency includes backend pacing waits and combines two execution stages. The comparison does not measure inference-only latency. The semantic reviews were not independently human-reviewed.

The continuation admitted **51 Gemini HTTP requests** under the persistent **100-request task cap** and the 10-RPM limiter. It reported 46,227 input and 1,955 output tokens, estimated at **USD 0.01876** at standard paid rates. Across both benchmark stages there were **99 observed model invocations**, 96 with reported usage, and 87,315 input plus 4,518 output tokens. The estimated benchmark cost for those reported tokens is **USD 0.03749**, excluding the separate historical smoke test. Earlier SDK invocations lack the new HTTP-level meter. Actual billing was not measured and no billing change or purchase was made. [Google pricing reference](https://aistudio.google.com/docs/pricing#gemini-3.5-flash-lite)

The final 44/44 HTTP count is a per-case result after resume. The interrupted first stage recorded 22/24 successful cases, so this metric cannot establish uninterrupted API reliability. Qwen remained the default. The later real administrator UAT verified cloud activation, actual Gemini chat and restoration to Qwen. Qwen keeps generation local; Gemini sends consented chat context to Google. Use only synthetic input in this demo.

[Scoring methodology](REPORT.md) · [Sanitized aggregate metrics](evidence/final-metrics.json) · [Reproduce the cloud benchmark](../docs/MODEL_SETTINGS.md)
