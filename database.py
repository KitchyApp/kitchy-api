"""
Database bootstrap for the Smart Kitchen backend.

Provides the SQLAlchemy engine, session factory, declarative Base, a lightweight
append-only column migration helper, and the FastAPI get_db dependency.
"""
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

    Column type strings are dialect-aware:
        - SQLite: BOOLEAN-like fields use INTEGER DEFAULT 0
        - PostgreSQL: BOOLEAN DEFAULT FALSE (required — INTEGER + Python
          bool binds cause "column is of type integer but expression is of
          type boolean" on INSERT, which broke POST /auth/register on Render)

    Nullable columns omit DEFAULT so the ADD is valid in strict PostgreSQL;
    non-nullable ones MUST carry a DEFAULT for the ADD to succeed.

    To register a new table, add an entry to the `schema` dict below and keep
    it in sync with the corresponding model file.
    """
    inspector = inspect(engine)

    # Dialect-aware boolean DDL for subscription / preference flags.
    bool_default = "INTEGER DEFAULT 0" if is_sqlite else "BOOLEAN DEFAULT FALSE"

    # ── Schema registry ──────────────────────────────────────────────────────
    # { table_name: [(column_name, "SQL_TYPE [DEFAULT value]"), ...] }
    # Entries in each list are append-only — existing columns are always skipped.
    schema: dict[str, list[tuple[str, str]]] = {

        # ── users (models/user.py) ────────────────────────────────────────────
        "users": [
            ("refresh_token_hash",  "VARCHAR"),
            ("plan",                "VARCHAR DEFAULT 'free'"),
            ("plan_expiry",         "TIMESTAMP"),
            ("is_premium",          bool_default),
            ("subscription_type",   "VARCHAR DEFAULT 'free'"),
            ("subscription_expires_at", "TIMESTAMP"),
            ("analyses_today",      "INTEGER DEFAULT 0"),
            ("last_analysis_date",  "DATE"),
            ("dietary_gluten_free", bool_default),
            ("dietary_vegetarian",  bool_default),
            ("dietary_vegan",       bool_default),
            ("preferred_style",     "VARCHAR DEFAULT 'balanced'"),
            ("preferred_cuisine",   "VARCHAR DEFAULT 'international'"),
            ("marketing_consent",   bool_default),
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
            ("is_premium_only",      bool_default),
            ("badge_code",           "VARCHAR DEFAULT '🏅'"),
            ("created_at",           "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
            # Weekly rotation fields — added after initial table creation.
            ("is_active",            "INTEGER DEFAULT 1" if is_sqlite else "BOOLEAN DEFAULT TRUE"),
            ("week_number",          "INTEGER"),
            ("week_year",            "INTEGER"),
            # culinary | barman — drives the Flutter Challenges TabBar.
            ("category",             "VARCHAR DEFAULT 'culinary'"),
        ],

        # ── user_challenge_progress (models/challenges.py) ────────────────────
        "user_challenge_progress": [
            ("user_id",      "INTEGER NOT NULL"),
            ("challenge_id", "INTEGER NOT NULL"),
            ("is_completed", bool_default),
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

    # Repair INTEGER→BOOLEAN mismatch on PostgreSQL for User.is_premium etc.
    # A previous migration used INTEGER DEFAULT 0; SQLAlchemy Boolean then
    # fails on INSERT with a type error during /auth/register.
    if not is_sqlite:
        _repair_postgres_boolean_columns()


def _repair_postgres_boolean_columns() -> None:
    """
    Convert legacy INTEGER flag columns on users to BOOLEAN on PostgreSQL.

    Safe / idempotent: only alters columns whose current udt_name is int2/int4/int8.
    """
    targets = (
        ("users", "is_premium"),
        ("users", "dietary_gluten_free"),
        ("users", "dietary_vegetarian"),
        ("users", "dietary_vegan"),
        ("users", "marketing_consent"),
    )
    try:
        with engine.begin() as conn:
            for table_name, col_name in targets:
                row = conn.execute(
                    text(
                        """
                        SELECT udt_name
                        FROM information_schema.columns
                        WHERE table_name = :table
                          AND column_name = :col
                        """
                    ),
                    {"table": table_name, "col": col_name},
                ).fetchone()
                if not row:
                    continue
                udt = (row[0] or "").lower()
                if udt not in {"int2", "int4", "int8", "integer", "smallint", "bigint"}:
                    continue
                logger.info(
                    "Schema repair: casting %s.%s from %s to BOOLEAN",
                    table_name, col_name, udt,
                )
                conn.execute(
                    text(
                        f"ALTER TABLE {table_name} "
                        f"ALTER COLUMN {col_name} DROP DEFAULT"
                    )
                )
                conn.execute(
                    text(
                        f"ALTER TABLE {table_name} "
                        f"ALTER COLUMN {col_name} TYPE BOOLEAN "
                        f"USING (COALESCE({col_name}, 0) <> 0)"
                    )
                )
                conn.execute(
                    text(
                        f"ALTER TABLE {table_name} "
                        f"ALTER COLUMN {col_name} SET DEFAULT FALSE"
                    )
                )
    except Exception as exc:
        # Never block startup — register will still try and surface a clear error.
        logger.warning("PostgreSQL boolean column repair skipped: %s", exc)


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
