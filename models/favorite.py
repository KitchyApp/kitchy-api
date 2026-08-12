"""SQLAlchemy model for user-saved (bookmarked) recipes."""

from datetime import datetime


from sqlalchemy import (
    String,
    Text,
    DateTime,
    ForeignKey,
)

from sqlalchemy.orm import (
    Mapped,
    mapped_column,
)

from database import Base


class Favorite(Base):
    """A recipe bookmark persisted for a specific user."""

    __tablename__ = "favorites"

    # Surrogate primary key.
    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
    )

    # Owner of this bookmark; references users.id.
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )

    # Display title shown in list views without deserialising recipe_data.
    recipe_title: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    # Full recipe payload serialised as JSON text.
    recipe_data: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # UTC timestamp when the user saved this recipe.
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )
