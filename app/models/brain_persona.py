import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class BrainPersona(Base):
    __tablename__ = "brain_persona"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tone: Mapped[str] = mapped_column(Text, server_default=text("'friendly'"), nullable=False)
    addressing: Mapped[str] = mapped_column(Text, server_default=text("'tykanie'"), nullable=False)
    language: Mapped[str] = mapped_column(Text, server_default=text("'sk'"), nullable=False)
    emoji_use: Mapped[str] = mapped_column(Text, server_default=text("'sometimes'"), nullable=False)
    length_preference: Mapped[str] = mapped_column(
        Text, server_default=text("'medium'"), nullable=False
    )
    rules: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"), nullable=False)
    negative_facts: Mapped[list] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("company_id", name="uq_brain_persona_company"),
    )
