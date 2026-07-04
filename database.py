import logging
import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

# ========================
# LOAD ENV
# ========================

load_dotenv()

logger = logging.getLogger(__name__)

# ========================
# DATABASE CONFIGURATION
# ========================

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    DATABASE_URL = "sqlite:///./app.db"
    logger.warning(
        "DATABASE_URL not set — falling back to local SQLite (app.db). "
        "Set DATABASE_URL in your .env for PostgreSQL."
    )

# ========================
# ENGINE SETUP
# ========================

is_sqlite = DATABASE_URL.startswith("sqlite")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if is_sqlite else {},
)

# ========================
# SESSION FACTORY
# ========================

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)

# ========================
# BASE MODEL CLASS
# ========================

Base = declarative_base()


# ========================
# SCHEMA MIGRATION HELPER
# ========================

def run_column_migrations() -> None:
    """
    Add any columns that exist in SQLAlchemy models but are missing from
    the physical database tables.

    Why this is needed:
        create_all() creates tables that don't exist yet, but it NEVER alters
        existing tables. When new columns are added to a model after the
        initial table was created, every SELECT fails immediately because
        SQLAlchemy includes all model columns in the query — even ones the DB
        doesn't have yet.

    This function acts as a lightweight, append-only migration layer:
        - It only ADDs columns; it never drops or renames anything.
        - It is safe to run on every startup (idempotent).
        - Tables that do not exist yet are skipped — create_all() handles them.
        - For a production-grade migration system, replace this with Alembic.

    Column type strings use generic SQL understood by both SQLite (permissive)
    and PostgreSQL (strict).
    Boolean columns use "INTEGER DEFAULT 0" so ADD COLUMN works in SQLite
    (which stores booleans as integers) and PostgreSQL alike.
    Nullable columns omit DEFAULT so the ADD is valid in strict PostgreSQL;
    non-nullable ones MUST carry a DEFAULT for the ADD to succeed.

    To register a new table, add an entry to the `schema` dict below and keep
    it in sync with the corresponding model file.
    """
    inspector = inspect(engine)

    # ── Schema registry ──────────────────────────────────────────────────────
    # { table_name: [(column_name, "SQL_TYPE [DEFAULT value]"), ...] }
    # Entries in each list are append-only — existing columns are always skipped.
    schema: dict[str, list[tuple[str, str]]] = {

        # ── users (models/user.py) ────────────────────────────────────────────
        "users": [
            ("refresh_token_hash",  "VARCHAR"),
            ("plan",                "VARCHAR DEFAULT 'free'"),
            ("plan_expiry",         "TIMESTAMP"),
            ("analyses_today",      "INTEGER DEFAULT 0"),
            ("last_analysis_date",  "DATE"),
            ("dietary_gluten_free", "INTEGER DEFAULT 0"),
            ("dietary_vegetarian",  "INTEGER DEFAULT 0"),
            ("dietary_vegan",       "INTEGER DEFAULT 0"),
            ("preferred_style",     "VARCHAR DEFAULT 'balanced'"),
            ("preferred_cuisine",   "VARCHAR DEFAULT 'international'"),
            ("marketing_consent",   "INTEGER DEFAULT 0"),
        ],

        # ── analytics_events (models/analytics.py) ────────────────────────────
        "analytics_events": [
            ("user_id",       "INTEGER"),
            ("event_name",    "VARCHAR NOT NULL"),
            ("metadata_json", "TEXT"),
            ("created_at",    "TIMESTAMP"),
        ],

        # ── ai_recipe_cache (models/ai_cache.py) ──────────────────────────────
        # ingredients_hash is the PK — created by create_all(), not here.
        # Entries below are for future columns added after initial table creation.
        "ai_recipe_cache": [
            ("recipe_json",      "TEXT NOT NULL"),
            ("created_at",       "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
        ],

        # ── chef_challenges (models/challenges.py) ────────────────────────────
        "chef_challenges": [
            ("title",                "VARCHAR NOT NULL"),
            ("required_ingredients", "VARCHAR NOT NULL"),
            ("is_premium_only",      "INTEGER DEFAULT 0"),
            ("badge_code",           "VARCHAR DEFAULT '🏅'"),
            ("created_at",           "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
        ],

        # ── user_challenge_progress (models/challenges.py) ────────────────────
        "user_challenge_progress": [
            ("user_id",      "INTEGER NOT NULL"),
            ("challenge_id", "INTEGER NOT NULL"),
            ("is_completed", "INTEGER DEFAULT 0"),
            ("completed_at", "TIMESTAMP"),
        ],
    }
    # ─────────────────────────────────────────────────────────────────────────

    total_added = 0

    for table_name, pending in schema.items():
        if not inspector.has_table(table_name):
            # New table — create_all() will create it; nothing to migrate yet.
            logger.debug("run_column_migrations: skipping '%s' (not yet created).", table_name)
            continue

        existing = {col["name"] for col in inspector.get_columns(table_name)}
        missing = [(name, defn) for name, defn in pending if name not in existing]

        if not missing:
            continue

        with engine.begin() as conn:
            for col_name, col_def in missing:
                logger.info(
                    "Schema migration: ALTER TABLE %s ADD COLUMN %s %s",
                    table_name, col_name, col_def,
                )
                conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_def}"))

        logger.info(
            "Schema migration: added %d column(s) to '%s'.",
            len(missing), table_name,
        )
        total_added += len(missing)

    if total_added:
        logger.info("Schema migration complete: %d column(s) added across all tables.", total_added)


# ========================
# DEPENDENCY (FASTAPI)
# ========================

def get_db():
    """
    FastAPI dependency that provides a database session per request.

    Lifecycle:
    - Opens a new session at the start of each request
    - Yields it to the endpoint handler
    - Always closes it in the finally block (prevents connection leaks)
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
