"""Storage for the global customer model preference."""

from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.database.models import Base


class ModelSetting(Base):
    __tablename__ = "model_settings"
    __table_args__ = (
        CheckConstraint("name = 'primary'", name="ck_model_settings_singleton"),
        CheckConstraint("provider IN ('local', 'cloud')", name="ck_model_settings_provider"),
    )

    name: Mapped[str] = mapped_column(String(16), primary_key=True)
    provider: Mapped[str] = mapped_column(String(8), nullable=False)
    cloud_model: Mapped[str | None] = mapped_column(String(256))
