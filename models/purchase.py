"""SQLAlchemy model for verified Google Play subscription purchases."""

from datetime import datetime

from sqlalchemy import (
    String,
    DateTime,
    ForeignKey,
)

from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class Purchase(Base):
    """Record of a validated in-app purchase linked to a user account."""

    __tablename__ = "purchases"

    # Surrogate primary key.
    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True
    )

    # Purchaser; references users.id.
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False
    )

    # Play Store product identifier (e.g. "premium_monthly").
    product_id: Mapped[str] = mapped_column(
        String,
        nullable=False
    )

    # Hashed purchase token — unique to prevent duplicate verification.
    purchase_token_hash: Mapped[str] = mapped_column(
        String,
        unique=True,
        nullable=False
    )

    # UTC datetime when the subscription period ends.
    expiry_date: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False
    )

    # UTC timestamp when this purchase row was first recorded.
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow
    )
