"""SQLAlchemy model for registered users, subscription state, and preferences."""

from sqlalchemy import (
    String,
    Integer,
    Boolean,
    Date,
    DateTime,
)

from sqlalchemy.orm import Mapped, mapped_column

from datetime import datetime

from database import Base


class User(Base):
    """Application user with authentication credentials, plan tier, and dietary prefs."""

    __tablename__ = "users"

    # Surrogate primary key.
    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
    )

    # Unique login identifier; indexed for fast lookup during auth.
    email: Mapped[str] = mapped_column(
        String,
        unique=True,
        index=True,
    )

    # Bcrypt (or equivalent) hash of the user's password — never store plain text.
    password: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    # Hashed refresh token for JWT rotation; null when the user is logged out.
    refresh_token_hash: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    # server_default is required alongside the Python-side default so that:
    # 1. New tables created via create_all() get the DB-level default.
    # 2. ALTER TABLE ADD COLUMN works on existing tables without data loss
    #    (a NOT NULL column can only be added with a default value).
    # 3. raw SQL INSERTs (e.g. from tests or scripts) don't need to specify
    #    every column explicitly.

    # Subscription tier: "free" or "premium".
    plan: Mapped[str] = mapped_column(
        String,
        default="free",
        server_default="free",
    )

    # UTC datetime when the premium plan expires; null for free users.
    plan_expiry: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    # Daily recipe-generation counter; reset when last_analysis_date rolls over.
    analyses_today: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
    )

    # Calendar date (UTC) of the last analysis; used to reset analyses_today.
    last_analysis_date: Mapped[Date | None] = mapped_column(
        Date,
        nullable=True,
    )

    # When True, generated recipes must exclude gluten-containing ingredients.
    dietary_gluten_free: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="0",
    )

    # When True, generated recipes must exclude meat and fish.
    dietary_vegetarian: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="0",
    )

    # When True, generated recipes must exclude all animal products.
    dietary_vegan: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="0",
    )

    # Recipe style preference passed to the AI prompt (e.g. "balanced", "quick").
    preferred_style: Mapped[str] = mapped_column(
        String,
        default="balanced",
        server_default="balanced",
    )

    # Preferred cuisine profile for recipe generation (e.g. "international").
    preferred_cuisine: Mapped[str] = mapped_column(
        String,
        default="international",
        server_default="international",
    )

    # Whether the user opted in to marketing communications.
    marketing_consent: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="0",
    )

    @property
    def is_premium(self) -> bool:
        """True when the user has an active Premium plan (not expired)."""
        plan = (self.plan or "").strip().lower()
        if plan != "premium":
            return False
        if self.plan_expiry is not None and self.plan_expiry < datetime.utcnow():
            return False
        return True
