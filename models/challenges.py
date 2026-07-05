"""
ChefChallenge  — curated cooking challenges, rotated weekly by the scheduler.
UserChallengeProgress — one row per user per challenge; tracks completion.

Weekly rotation fields
----------------------
is_active    : Only active challenges are returned by GET /challenges.
               The scheduler sets this to False for the previous week's
               challenges before inserting the new week's selection.
week_number  : ISO week number (1-53) during which the challenge was activated.
week_year    : ISO year — needed to disambiguate week 1 across year boundaries.
               Both fields together form a unique week identifier.

Rotation contract
-----------------
- 1 Free  challenge is selected from the pool each week.
- 2 Premium challenges are selected from the pool each week.
- Rotation fires on Monday 00:00 UTC (or on server startup when no active
  challenges exist for the current ISO week).
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class ChefChallenge(Base):
    __tablename__ = "chef_challenges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # Human-readable challenge name shown in the UI.
    title: Mapped[str] = mapped_column(String, nullable=False)

    # Comma-separated list of required ingredient slugs (lowercase, no spaces).
    # Example: "grao-de-bico,espinafres,alho"
    # The verify endpoint checks that every token here appears in the
    # recipe's ingredient list (case-insensitive, partial match allowed).
    required_ingredients: Mapped[str] = mapped_column(String, nullable=False)

    # When True, the challenge is only available to Premium subscribers.
    # Free users see the challenge card but it is rendered as locked.
    is_premium_only: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="0",
    )

    # Short code / emoji for the badge awarded on completion.
    badge_code: Mapped[str] = mapped_column(
        String,
        default="🏅",
        server_default="🏅",
    )

    # ── Weekly rotation fields ─────────────────────────────────────────────────

    # False once the scheduler replaces this challenge with a newer one.
    # GET /challenges filters WHERE is_active = 1.
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="1",
    )

    # ISO week number in which this challenge was made active (nullable for
    # legacy rows inserted before the rotation system was added).
    week_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ISO year matching week_number — required to handle week-1 of a new year.
    week_year: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        server_default=func.now(),
    )


class UserChallengeProgress(Base):
    __tablename__ = "user_challenge_progress"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    challenge_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("chef_challenges.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    is_completed: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="0",
    )

    # Set when is_completed flips to True for the first time.
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
