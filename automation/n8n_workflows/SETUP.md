# LeadCreated follow-up workflow

Follow the [isolated Compose demo](../../docs/DEMO.md#5-deliver-the-event-and-verify-the-n8n-task) to import [the workflow](lead_created_follow_up.json), set the Header Auth and PostgreSQL credentials, publish, and start the worker. Use the dedicated database role with permission to execute the follow-up function.

The workflow persists or verifies one internal follow-up task per Outbox Event before acknowledging delivery. It disables execution payload storage because incoming headers may include the shared secret. Use synthetic data when testing and avoid capturing credential settings.
