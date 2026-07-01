"""
Model: AnalyticsEvent

Lightweight behavioural analytics table.

Each row records a single named event triggered by a user (or anonymously).
The metadata_json TEXT column stores arbitrary JSON so the schema never needs
to be altered when new event types require extra context fields.

Usage:
    Use services.analytics_service.log_analytics_event() — never write to this
    table directly from endpoint handlers.

Event catalogue (event_name values in use):
    "limit_blocked_403"   — quota enforced in /generate-recipes/
    "premium_converted"   — successful plan upgrade in /billing/verify-purchase
    "recipe_generated"    — (future) every successful recipe generation
    "share_clicked"       — (future) user tapped share on a recipe
    "paywall_view"        — (future) user saw the upgrade screen
"""

from datetime import datetime

from sqlalchemy import Integer, String, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
    )

    # nullable=True so events can be recorded before a user logs in (future).
    user_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    # Short identifier for the event type — keep these snake_case strings
    # consistent; they are the primary grouping key for analytics queries.
    event_name: Mapped[str] = mapped_column(
        String,
        nullable=False,
        index=True,
    )

    # Arbitrary JSON string for event-specific context.
    # Stored as TEXT to avoid JSON column type differences between SQLite
    # (which has no native JSON type) and PostgreSQL (which has JSONB).
    # Deserialise with json.loads() when reading.
    metadata_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )
