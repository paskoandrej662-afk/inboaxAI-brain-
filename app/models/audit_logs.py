import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    channel: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    query: Mapped[str | None] = mapped_column(Text, nullable=True)
    route: Mapped[str | None] = mapped_column(Text, nullable=True)

    retrieved_chunk_ids: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=True
    )
    retrieved_facts: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    cited_sources: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    used_chunk_ids: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=True
    )

    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_versions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    needs_human: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False
    )
    flags: Mapped[dict] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "ix_audit_logs_company_created",
            "company_id",
            text("created_at DESC"),
        ),
    )
