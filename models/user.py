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
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
    )

    email: Mapped[str] = mapped_column(
        String,
        unique=True,
        index=True,
    )

    password: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

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

    plan: Mapped[str] = mapped_column(
        String,
        default="free",
        server_default="free",
    )

    plan_expiry: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    analyses_today: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
    )

    last_analysis_date: Mapped[Date | None] = mapped_column(
        Date,
        nullable=True,
    )

    dietary_gluten_free: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="0",
    )

    dietary_vegetarian: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="0",
    )

    dietary_vegan: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="0",
    )

    preferred_style: Mapped[str] = mapped_column(
        String,
        default="balanced",
        server_default="balanced",
    )

    preferred_cuisine: Mapped[str] = mapped_column(
        String,
        default="international",
        server_default="international",
    )

    marketing_consent: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="0",
    )
