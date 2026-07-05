"""
ChefChallenge  — curated cooking challenges seeded by admins.
UserChallengeProgress — one row per user per challenge; tracks completion.
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

    # Short emoji / icon code awarded when the challenge is completed.
    # Example: "🥗", "🌱", "🏆"
    badge_code: Mapped[str] = mapped_column(
        String,
        default="🏅",
        server_default="🏅",
    )

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
