from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
)

from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
)

from pgvector.sqlalchemy import VECTOR


class Base(DeclarativeBase):
    pass


# --------------------------------------------------
# Properties
# --------------------------------------------------

class PropertyRecord(Base):

    __tablename__ = "properties"

    property_id: Mapped[str] = mapped_column(
        String(20),
        primary_key=True,
    )

    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    location_code: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )

    display_location: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    property_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    price_myr: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    bedrooms: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    bathrooms: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    mrt_distance_m: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    availability_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "price_myr >= 0",
            name="ck_properties_price",
        ),
        CheckConstraint(
            "bedrooms >= 0 AND bathrooms >= 0",
            name="ck_properties_rooms",
        ),
        CheckConstraint(
            "mrt_distance_m IS NULL OR mrt_distance_m >= 0",
            name="ck_properties_mrt",
        ),
        CheckConstraint(
            "property_type IN ('CONDO', 'APARTMENT', 'LANDED')",
            name="ck_properties_type",
        ),
        CheckConstraint(
            "availability_status IN ('AVAILABLE', 'RESERVED', 'SOLD')",
            name="ck_properties_availability",
        ),
        CheckConstraint(
            "location_code IN ('KUALA_LUMPUR', 'SELANGOR', 'JOHOR_BAHRU')",
            name="ck_properties_location",
        ),
    )


# --------------------------------------------------
# Documents
# --------------------------------------------------

class DocumentRecord(Base):

    __tablename__ = "documents"

    document_id: Mapped[str] = mapped_column(
        String(50),
        primary_key=True,
    )

    property_id: Mapped[str | None] = mapped_column(
        ForeignKey("properties.property_id"),
        nullable=True,
        index=True,
    )

    filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    page_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )

    __table_args__ = (
        CheckConstraint(
            "page_count >= 1",
            name="ck_documents_page_count",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_documents_version",
        ),
    )


# --------------------------------------------------
# Document Chunks
# --------------------------------------------------

class DocumentChunkRecord(Base):

    __tablename__ = "document_chunks"

    chunk_id: Mapped[str] = mapped_column(
        String(100),
        primary_key=True,
    )

    document_id: Mapped[str] = mapped_column(
        ForeignKey(
            "documents.document_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    page_num: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    chunk_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    embedding_model: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    embedding_model_version: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    embedding: Mapped[list] = mapped_column(
        VECTOR(1024),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "page_num >= 1",
            name="ck_chunks_page",
        ),
        CheckConstraint(
            "chunk_number >= 1",
            name="ck_chunks_number",
        ),
    )
