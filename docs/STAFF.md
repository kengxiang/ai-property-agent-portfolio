# Sandbox staff dashboard

Enable the administration flag in the synthetic Sandbox. Provision a staff account interactively with `dc exec backend python -m backend.create_sandbox_admin --role staff`. Open **Staff Dashboard** in the sidebar and sign in. Administrators can also sign in. Staff credentials cannot access property ingestion.

## How the records relate

A Lead is a confirmed customer enquiry. Its confirmation request is recorded once by idempotency key. The same database transaction writes a LeadCreated Outbox event for that lead. The worker delivers the event and n8n uses its ID to persist or verify the internal Follow-up Task. A delivered event is an automation outcome; employees complete the task separately.

A unique database key guarantees at most one event per event type and lead. A second key guarantees at most one task per event. The dashboard uses only the LeadCreated event for this workflow. Other event types do not change the lead's status. A task must match the event, lead, property, and supported task state. An inconsistent task link is flagged and is excluded from the derived status.

## Lead and automation statuses

| Lead follow-up status | Meaning |
|---|---|
| PENDING | A valid task exists and an employee has yet to start it. |
| IN_PROGRESS | An employee started the valid task. |
| COMPLETED | An employee completed the valid task. |
| AUTOMATION_FAILED | The LeadCreated event is terminal FAILED and lacks a valid task. |
| NO_TASK | No valid task is available and terminal failure is unconfirmed. The row can explain queued delivery, processing, retry, missing records, or a delivered event missing its task. |

A valid task takes precedence over a delivery error. Use **Inspect lead** to view the associated task or investigate its delivery. Lists are paginated. **Follow-up tasks** retains the existing Start and Complete actions. A status change atomically saves the state, timestamp, and last acting employee. A stale update receives a conflict and can be refreshed.

In **Automation**, inspect the attempt count, property, related lead, valid task, delivery state and sanitized error. **Requires Attention** identifies a terminal FAILED event without a valid task and a DELIVERED event missing its valid task. In-flight events retain their actual state. Use **Investigate event** for available dates and details. The outbox stores no last-failure timestamp, so this field displays **Not recorded**. Error summaries do not establish historical root causes.

## Overview metrics

- **Total Leads** counts confirmed lead rows.
- **Pending, In progress and Completed Tasks** count follow-up task rows in each stored state.
- **Delivered and Failed Events** count LeadCreated events in those final states.
- **Requires Attention** counts terminal LeadCreated events missing a valid matching task, including the delivered-without-task anomaly.
- **Overall Event Delivery Rate** is DELIVERED divided by (DELIVERED + FAILED), expressed as a percentage. Events still queued, sending or retrying are excluded. A zero denominator displays N/A. Historical events contribute to the rate, which has no time-window interpretation.

Monitoring is read-only. The service retains the same staff authentication and excludes raw payloads, authentication data and stack traces from monitoring responses.
