"""Persistent Lead model used only after explicit confirmation (P2-03)."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.database.models import Base


class LeadRecord(Base):
    __tablename__ = "leads"

    lead_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    property_id: Mapped[str] = mapped_column(
        String(20), ForeignKey("properties.property_id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    customer_name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_purpose: Mapped[str] = mapped_column(String(255), nullable=False)
    consented_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="NEW",
    )
    source: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="STREAMLIT",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
