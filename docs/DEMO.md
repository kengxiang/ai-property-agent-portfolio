# Local demo

The demo uses the dedicated Compose project `ai-property-portfolio`, separate named volumes and loopback ports. Run from the repository root with Bash, Docker Engine with Compose v2 and host Ollama. On first use, check that the project name and its volumes are unused. If they already belong to another installation, use a separate Docker context or host.

## Start and seed the Sandbox

From the repository root:

```bash
ollama pull qwen3.5:4b
ollama pull bge-m3
(umask 077; cp -n .env.example .env.compose)
chmod 600 .env.compose
docker ps -a --filter label=com.docker.compose.project=ai-property-portfolio
docker volume ls --filter label=com.docker.compose.project=ai-property-portfolio
```

Edit `.env.compose` locally. Set a demo-only `POSTGRES_PASSWORD` and a distinct `N8N_WEBHOOK_SECRET`. Set `COMPOSE_API_PORT=38000`, `COMPOSE_FRONTEND_PORT=38501`, `COMPOSE_N8N_PORT=35678`, and `ADMIN_INGESTION_ENABLED=1`. Confirm the ports are free. Use `COMPOSE_OLLAMA_BASE_URL` if the default `http://host.docker.internal:11434` is unreachable from Docker. The backend maps this name to the Docker host gateway on Linux. Configure Ollama to listen on an interface reachable from Docker while restricting access to the local host and Docker network. Confirm the connection with the check below before importing embeddings.

```bash
dc() { docker compose --env-file .env.compose -p ai-property-portfolio "$@"; }
dc config --quiet
dc build backend
dc up -d --wait
curl -fsS http://127.0.0.1:38000/health
curl -fsS http://127.0.0.1:38501/_stcore/health
curl -fsS http://127.0.0.1:35678/healthz/readiness
dc exec -T backend python -c 'import os; from urllib.request import urlopen; print(urlopen(os.environ["OLLAMA_BASE_URL"].rstrip("/") + "/api/tags", timeout=5).status)'
```

Alembic creates the schema on startup. The image includes the fixture CSV and PDFs. Run the existing importer against the Sandbox named volume to load the catalog and build the document embeddings:

```bash
(
  set -euo pipefail
  id=$(dc ps -q postgres)
  test -n "$id"
  test "$(docker inspect "$id" --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}')" = ai-property-portfolio_postgres_data
  dc exec -T backend python -m scripts.import_postgres --only all --rebuild-document-index
)
```

## 1. Search properties

Open Streamlit at `http://127.0.0.1:38501` and select the local Qwen model. Ask “List available Selangor property IDs costing RM350000 or less.” The bundled catalog returns P014. P018 is reserved and stays outside this search. The catalog API is also available at `http://127.0.0.1:38000/docs`.

## 2. Ask a document-grounded question

Ask “Are pets allowed at P001? Cite the filename and page.” The supporting rule allows pets with written management approval and is on page 1 of `P001_house_rules.pdf`. Ask the same question for P002 and verify that the answer follows P002's rule prohibiting pets. P022 has no supporting pet policy.

## 3. Prepare and confirm an enquiry

Select P022 in **Property Enquiry**. Use a made-up name such as `Demo Customer`, the fictitious demo number `0000000000`, and **Synthetic viewing enquiry** as the message. Select **Review Enquiry**, check the draft, grant consent, and select **Confirm & Submit**. Record the displayed lead ID. Keep the worker stopped until the webhook is configured.

## 4. Verify the lead and event

Open PostgreSQL in the Sandbox service. Replace `PASTE_DEMO_LEAD_ID_HERE` with the displayed ID. The queries show only identifiers and workflow status.

```bash
dc exec postgres sh -c 'psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

```sql
SELECT lead_id, property_id FROM leads WHERE lead_id = 'PASTE_DEMO_LEAD_ID_HERE';
SELECT event_id, delivery_status, attempts FROM outbox_events
WHERE aggregate_id = 'PASTE_DEMO_LEAD_ID_HERE';
```

Before enabling the worker, expect one lead and a PENDING event. Exit with `\q`.

## 5. Deliver the event and verify the n8n task

Provision a dedicated login from the Sandbox PostgreSQL session, using a private demo password at the prompt:

```sql
CREATE ROLE property_follow_up_n8n LOGIN;
\password property_follow_up_n8n
GRANT USAGE ON SCHEMA public TO property_follow_up_n8n;
GRANT EXECUTE ON FUNCTION public.process_lead_follow_up(text, text, text, text)
TO property_follow_up_n8n;
```

Open n8n at `http://127.0.0.1:35678`, create the local owner, and import [the workflow](../automation/n8n_workflows/lead_created_follow_up.json). In **LeadCreated Webhook**, select a Header Auth credential named as desired, with header `X-Webhook-Secret` and the same value as `N8N_WEBHOOK_SECRET`. In **Persist or Verify Follow-up**, select a PostgreSQL credential with host `property-db`, port `5432`, the configured database name, and the dedicated login. The isolated local setup uses SSL disabled for this connection. Publish the workflow before starting the worker.

```bash
dc --profile automation up -d worker
dc --profile automation logs --tail=40 worker
```

Check the same event again in PostgreSQL. Expect DELIVERED and exactly one task:

```sql
SELECT o.event_id, o.delivery_status, o.attempts, t.task_id, t.status AS task_status
FROM outbox_events AS o
LEFT JOIN follow_up_tasks AS t ON t.event_id = o.event_id
WHERE o.aggregate_id = 'PASTE_DEMO_LEAD_ID_HERE';
```

## 6. Demonstrate admin ingestion

```bash
dc exec backend python -m backend.create_sandbox_admin
```

Enter a local account name and a password of at least 14 characters. Open **Administration** from the Streamlit sidebar and sign in. Add a synthetic property using an ID absent from the catalog. For CSV, copy the header and one row from [the synthetic fixture](../data/synthetic_properties.csv), change the ID to another unused value, and upload the UTF-8 file. Select **Preview CSV**, then **Confirm Import**. Check the created count in the UI and verify the new ID via `GET /properties/{property_id}`. Log out when finished.

## 7. Complete the follow-up as staff

Create a separate employee account using `dc exec backend python -m backend.create_sandbox_admin --role staff`. In the Streamlit sidebar open **Staff Dashboard** and sign in. Find the synthetic lead in **Leads**, then locate its task in **Follow-up tasks**. Select **Start task**, refresh, select **Complete task**, and refresh again. Verify COMPLETED, the employee username, and the update timestamp. **Automation** should show Delivered and link the event to the task. See the [metric definitions](STAFF.md).

An administrator can inspect the configured provider under **Administration → Model settings**. See [model setup](MODEL_SETTINGS.md) for a controlled cloud evaluation.

Stop the Sandbox while preserving its named volumes:

```bash
dc --profile automation stop
```

## Verification

The public snapshot was built and started with fresh demo volumes. Backend, Streamlit and n8n health checks passed. The importer created 24 synthetic properties, six PDF records and six BGE-M3 vector chunks. Live Qwen and BGE-M3 verified the P022 price and P001 page-one citation. A browser run also exercised search and the citation view.

AppTest confirmed a synthetic enquiry, replayed the request without duplicating the lead, and exercised the actual worker and n8n workflow. The event reached DELIVERED and produced one task. Staff started and completed the task. Refresh and an independent API read confirmed persistence. A preliminary check with a mismatched test database name returned HTTP 503. The successful check used the correct database and a fresh synthetic enquiry.

Run the standard suite against an already started, isolated demo Compose database via:

```bash
dc run --rm --no-deps   -v "$PWD/tests:/app/tests:ro" -v "$PWD/evaluation:/app/evaluation:ro"   -e RUN_POSTGRES_TESTS=1 -e PROPERTY_BACKEND=csv -e RAG_BACKEND=   backend python -m pytest -q -rs
```

The public snapshot recorded 247 passed and three opt-in skips. The separate real stack check above covers the demo workflow against deployed services.
