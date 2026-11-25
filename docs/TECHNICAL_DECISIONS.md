# Technical decisions

| Decision | Rationale and trade-off |
|---|---|
| Single LangGraph agent | One graph is sufficient for the three related read tools and keeps retries and tool traces in one place. The model still chooses tools, so the same request can produce different calls or wording. |
| Structured calls and backend validation | Pydantic schemas constrain inputs. Server-side availability checks prevent the model from widening a search on its own. An invalid call may be repaired once. Recognition of explicit availability and property-type language is limited to supported patterns. |
| Property-isolated RAG | Apply the property filter before ranking chunks. This reduces cross-property evidence leakage and preserves a useful source trail. A correct citation does not guarantee that every claim follows from the cited page. |
| Local Qwen and BGE-M3 | Running generation and embeddings through Ollama makes the demo usable without cloud credentials. Speed and output quality depend on the local model, available memory, and hardware. The Gemini path adds cloud availability and quota constraints. The measured comparison includes a resumed cloud run. |
| PostgreSQL and pgvector | Catalog, leads, audit records, workflow state, and vectors share a database with transactional guarantees. PostgreSQL requires migrations and operational care. The evaluation uses a small corpus and does not establish vector search performance at scale. |
| Transactional Outbox | Commit the lead and its event together, then make the network request asynchronously. The lead survives workflow downtime. Delivery is eventual and can repeat after a timeout or worker interruption. |
| Idempotency at both boundaries | The confirmation key lets a client retry the same request without creating another lead. A unique event ID lets n8n safely handle repeated delivery. The two keys protect different operations. |
| Docker Compose isolation | A separate project, named volumes, loopback ports, and local credentials isolate the demo. The worker is opt-in, so queued events can wait until webhook setup. Compose provides local orchestration. Multi-host operations, transport security, and production access control remain separate work. |

## P4 source-of-truth decisions

The P4 scope extends the existing single-agent design with employee operations and explicit administrator model selection. The customer agent retains the same read-only tool boundary.

Task management reuses the n8n follow-up row and its timestamp column. One additive migration records the latest employee. A conditional PostgreSQL update rejects stale or invalid transitions. The dashboard exposes operational summaries from an explicit field list, including a controlled mapping for error codes.

The global provider preference is one PostgreSQL row, with Qwen as the default when the row is absent. Gemini activation verifies the configured model with a synthetic tool call. The customer portal does not control the setting. Conversation consent and contact-pattern checks gate cloud chat, and provider failures are reported to the caller.
