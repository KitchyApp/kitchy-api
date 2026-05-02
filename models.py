from sqlalchemy import Column, Integer, String, Date, Boolean, DateTime, ForeignKey
from database import Base
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime


class User(Base):
    """
        User model representing application users.

        Fields:
        - id: Primary key
        - email: Unique user email
        - password: Hashed password
        - refresh_token: Stored refresh token for session management
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    # Unique email identifier
    email: Mapped[str] = mapped_column(String, unique=True, index=True)

    # Hashed password
    password: Mapped[str] = mapped_column(String, nullable=False)  # enforce not null

    # Refresh token stored for session validation
    refresh_token_hash: Mapped[str | None] = mapped_column(String, nullable=True)

    # Subscription plan (free / premium)
    plan: Mapped[str] = mapped_column(String, default="free")
    plan_expiry: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Daily usage tracking
    analyses_today: Mapped[int] = mapped_column(Integer, default=0)
    last_analysis_date: Mapped[Date | None] = mapped_column(Date, nullable=True)

    # Dietary preferences
    dietary_gluten_free: Mapped[bool] = mapped_column(Boolean, default=False)
    dietary_vegetarian: Mapped[bool] = mapped_column(Boolean, default=False)
    dietary_vegan: Mapped[bool] = mapped_column(Boolean, default=False)

    preferred_style: Mapped[str] = mapped_column(String, default="balanced")
    preferred_cuisine: Mapped[str] = mapped_column(String, default="international")

    # GDPR / marketing consent
    marketing_consent: Mapped[bool] = mapped_column(Boolean, default=False)


class Purchase(Base):
    """
    Purchase model for subscription validation (Google Play).

    Purpose:
    - Stores validated purchases from external providers (Google Play)

    Security:
    - purchase_token is NEVER stored raw
    - Only SHA256 hash is persisted

    Fields:
    - user_id: owner of the purchase
    - product_id: subscription identifier (Google Play)
    - purchase_token_hash: unique hash (prevents reuse/fraud)
    - expiry_date: subscription expiration timestamp
    """

    __tablename__ = "purchases"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False
    )

    product_id: Mapped[str] = mapped_column(String, nullable=False)

    purchase_token_hash: Mapped[str] = mapped_column(
        String,
        unique=True,
        nullable=False
    )

    expiry_date: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow
    )
