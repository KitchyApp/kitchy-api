"""
AiRecipeCache — persistent DB-level recipe cache.

Purpose
-------
Store OpenAI-generated recipe JSON keyed by a deterministic string that
encodes the exact inputs that affect the model's output:

    "<sorted_ingredients>|<num_recipes>r|lang=<locale>|gf=<0|1>|vg=<0|1>|vn=<0|1>|c=<cuisine>"

Example key:
    "arroz,brocolos,frango|1r|lang=pt-PT|gf=0|vg=0|vn=0|c=international"

Why include plan/prefs in the key:
- A free user generates 1 recipe; a premium user generates 4.
  Without the plan tier in the key, the first requester's quota would
  determine what every subsequent user receives for those ingredients.
- Dietary restrictions (vegan, vegetarian, gluten-free) change the prompt
  hard-rules. Two users with different restrictions must get distinct entries.
- Cuisine adapts flavour profile and plating — must be part of the key.

Cache invalidation:
- No automatic TTL. Rows survive until manually purged.
- Add a periodic cleanup job (cron / Celery beat) if staleness becomes an
  issue in production (e.g. DELETE WHERE created_at < NOW() - INTERVAL '30 days').
"""

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class AiRecipeCache(Base):
    __tablename__ = "ai_recipe_cache"

    # The full cache key is the primary key — direct equality lookup, O(log n).
    # VARCHAR(768) comfortably fits the longest realistic key (~200 chars) while
    # staying within MySQL/PostgreSQL index-key limits.
    ingredients_hash: Mapped[str] = mapped_column(
        String(768),
        primary_key=True,
    )

    # Serialised JSON list of recipe dicts, exactly as returned by generate_recipes().
    recipe_json: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        server_default="CURRENT_TIMESTAMP",
    )
