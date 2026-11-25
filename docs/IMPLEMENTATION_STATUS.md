# Implementation status

| Area | Verified scope |
|---|---|
| Property assistant | Streamlit and FastAPI serve structured search and details. LangGraph calls read-only tools and accepts recent conversation context. |
| Document Q&A | Bundled synthetic PDFs, BGE-M3 embeddings, PostgreSQL/pgvector retrieval, property-level filtering, and filename/page citations. |
| Admin ingestion | Sandbox-only demo with interactive account provisioning, bearer sessions, validated property creation, CSV preview and confirmation, and audit records. |
| Lead automation | Explicit customer consent, idempotent confirmation, transactional Outbox, separate worker, authenticated n8n webhook, and durable internal follow-up task. |
| Local deployment | Docker Compose manages Streamlit, API, migrations, PostgreSQL, n8n, and the opt-in worker. Ollama runs on the host. |
| Employee operations | Role-gated lead inspection with derived follow-up status, task management, and read-only automation investigation. See the [staff dashboard guide](STAFF.md). |
| Provider selection | Administrator control stored in PostgreSQL. Qwen → Gemini → Qwen verified through automated Streamlit AppTest with real providers, persisted settings and a synthetic customer chat. |
| Evaluation | Three final 44-case local-model runs, one resumed 44-case Gemini evaluation, and a historical Sandbox regression suite. See the [evaluation report](../evaluation/REPORT.md). |

Open work includes manual browser acceptance and broader independently reviewed evaluation data, production deployment controls and monitoring, and a controlled ingestion path for administrator-uploaded PDFs.
