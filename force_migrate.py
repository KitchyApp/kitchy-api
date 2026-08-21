"""
Temporary one-shot schema repair for production PostgreSQL.

Ensures the users table has the subscription columns required by models/user.py.
Safe to run on every startup: each ALTER only runs when the column is missing.

Remove this file (and its call from main.py) once Alembic or a stable
migration history is in place.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text

from database import engine

logger = logging.getLogger(__name__)

# Columns that must exist for POST /auth/register and billing sync.
_USERS_COLUMNS: list[tuple[str, str]] = [
    ("is_premium", "BOOLEAN DEFAULT FALSE"),
    ("subscription_type", "VARCHAR DEFAULT 'free'"),
    ("subscription_expires_at", "TIMESTAMP"),
]


def force_migrate_users_subscription_columns() -> None:
    """
    Add missing subscription columns on users via ALTER TABLE.

    Idempotent: skips columns that already exist. Does nothing if the users
    table has not been created yet (create_all handles that case).
    """
    inspector = inspect(engine)

    if not inspector.has_table("users"):
        logger.info(
            "force_migrate: skipping — table 'users' does not exist yet."
        )
        return

    existing = {col["name"] for col in inspector.get_columns("users")}
    missing = [(name, ddl) for name, ddl in _USERS_COLUMNS if name not in existing]

    if not missing:
        logger.info("force_migrate: users subscription columns already present.")
        return

    with engine.begin() as conn:
        for col_name, col_ddl in missing:
            sql = f"ALTER TABLE users ADD COLUMN {col_name} {col_ddl}"
            logger.info("force_migrate: %s", sql)
            conn.execute(text(sql))

    logger.info(
        "force_migrate: added %d column(s) to users: %s",
        len(missing),
        ", ".join(name for name, _ in missing),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    force_migrate_users_subscription_columns()
    print("force_migrate: done.")
