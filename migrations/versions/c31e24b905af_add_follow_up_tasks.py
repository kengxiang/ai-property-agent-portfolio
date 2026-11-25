"""Persist internal follow-up tasks and grant narrow access through a database function."""

import sqlalchemy as sa
from alembic import op

revision = "c31e24b905af"
down_revision = "b8c2d1e4f607"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "follow_up_tasks",
        sa.Column("task_id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("outbox_events.event_id", ondelete="RESTRICT"),
                  nullable=False, unique=True),
        sa.Column("lead_id", sa.String(36), sa.ForeignKey("leads.lead_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("property_id", sa.String(20), sa.ForeignKey("properties.property_id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("task_type", sa.String(30), server_default="FOLLOW_UP", nullable=False),
        sa.Column("status", sa.String(30), server_default="PENDING", nullable=False),
        sa.Column("assigned_to", sa.String(100)),
        sa.Column("due_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.execute("""
        CREATE FUNCTION public.process_lead_follow_up(
            incoming_event text, incoming_type text, incoming_lead text, incoming_property text
        ) RETURNS integer
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $function$
        DECLARE
            source_event public.outbox_events%ROWTYPE;
            task public.follow_up_tasks%ROWTYPE;
        BEGIN
            IF incoming_event IS NULL OR incoming_event !~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$'
                OR incoming_lead IS NULL OR incoming_lead !~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$'
                OR incoming_type IS DISTINCT FROM 'LeadCreated'
                OR incoming_property IS NULL OR char_length(incoming_property) NOT BETWEEN 1 AND 20
                OR incoming_property ~ '^[[:space:]]*$'
            THEN
                RETURN 400;
            END IF;

            SELECT * INTO source_event
            FROM public.outbox_events
            WHERE event_id = incoming_event;

            IF NOT FOUND THEN
                RETURN 404;
            END IF;

            IF source_event.event_type IS DISTINCT FROM incoming_type
                OR source_event.aggregate_id IS DISTINCT FROM incoming_lead
                OR source_event.payload ->> 'lead_id' IS DISTINCT FROM incoming_lead
                OR source_event.payload ->> 'property_id' IS DISTINCT FROM incoming_property
            THEN
                RETURN 409;
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM public.leads AS lead
                JOIN public.lead_requests AS confirmation ON confirmation.lead_id = lead.lead_id
                WHERE lead.lead_id = incoming_lead AND lead.property_id = incoming_property
            ) THEN
                RETURN 409;
            END IF;

            INSERT INTO public.follow_up_tasks (task_id, event_id, lead_id, property_id)
            VALUES (gen_random_uuid()::text, incoming_event, incoming_lead, incoming_property)
            ON CONFLICT (event_id) DO NOTHING;

            -- A separate statement at READ COMMITTED sees a concurrently committed winning row.
            SELECT * INTO task FROM public.follow_up_tasks WHERE event_id = incoming_event;
            IF FOUND AND task.lead_id = incoming_lead AND task.property_id = incoming_property
                AND task.task_type = 'FOLLOW_UP'
            THEN
                RETURN 200;
            END IF;
            RETURN 409;
        END;
        $function$
    """)
    op.execute("REVOKE ALL ON FUNCTION public.process_lead_follow_up(text, text, text, text) FROM PUBLIC")


def downgrade():
    op.execute("DROP FUNCTION public.process_lead_follow_up(text, text, text, text)")
    op.drop_table("follow_up_tasks")
