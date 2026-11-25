from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.database.models import Base


class LeadRequestRecord(Base):
    """Persistent idempotency record for confirmed leads."""

    __tablename__ = "lead_requests"

    idempotency_key: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
    )

    request_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    lead_id: Mapped[str] = mapped_column(
        ForeignKey("leads.lead_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class OutboxEventRecord(Base):
    """Event persisted in the same transaction as a lead."""

    __tablename__ = "outbox_events"

    event_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )

    event_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    aggregate_id: Mapped[str] = mapped_column(
        ForeignKey("leads.lead_id", ondelete="RESTRICT"),
        nullable=False,
    )

    payload: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
    )

    delivery_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="PENDING",
        index=True,
    )

    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_token: Mapped[str | None] = mapped_column(String(36))

    __table_args__ = (
        UniqueConstraint(
            "event_type",
            "aggregate_id",
            name="uq_outbox_event_aggregate",
        ),
        CheckConstraint(
            "attempts >= 0",
            name="ck_outbox_attempts",
        ),
        CheckConstraint(
            "delivery_status IN "
            "('PENDING', 'PROCESSING', 'DELIVERED', 'RETRY', 'FAILED')",
            name="ck_outbox_delivery_status",
        ),
    )


class FollowUpTaskRecord(Base):
    """Durable internal task created once per confirmed LeadCreated event."""

    __tablename__ = "follow_up_tasks"

    task_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_id: Mapped[str] = mapped_column(
        ForeignKey("outbox_events.event_id", ondelete="RESTRICT"), unique=True,
    )
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.lead_id", ondelete="RESTRICT"))
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.property_id", ondelete="RESTRICT"))
    task_type: Mapped[str] = mapped_column(String(30), server_default="FOLLOW_UP")
    status: Mapped[str] = mapped_column(String(30), server_default="PENDING")
    assigned_to: Mapped[str | None] = mapped_column(String(100))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str | None] = mapped_column(String(64))
