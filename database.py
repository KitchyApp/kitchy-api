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
    Add any columns that exist in the SQLAlchemy model but are missing from
    the physical database table.

    Why this is needed:
        create_all() creates tables that don't exist yet, but it NEVER alters
        existing tables. When new columns are added to the User model after the
        initial table was created, every SELECT fails immediately because
        SQLAlchemy includes all model columns in the query — even ones the DB
        doesn't have yet.

    This function acts as a lightweight, append-only migration layer:
        - It only ADDs columns; it never drops or renames anything.
        - It is safe to run on every startup (idempotent).
        - For a proper migration system, replace this with Alembic.

    Column type strings use generic SQL that is understood by both
    SQLite (permissive) and PostgreSQL (strict).
    Boolean columns use "INTEGER DEFAULT 0" so the ADD COLUMN works in
    SQLite (which stores booleans as integers) and PostgreSQL alike.
    """
    inspector = inspect(engine)

    if not inspector.has_table("users"):
        # Table will be created by create_all() — nothing to migrate.
        return

    existing = {col["name"] for col in inspector.get_columns("users")}

    # (column_name, SQL type + default)
    # Keep in sync with models/user.py.
    # Nullable columns omit DEFAULT so the ADD COLUMN is valid even in strict
    # PostgreSQL; non-nullable ones MUST have a DEFAULT for the ADD to succeed.
    pending = [
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
    ]

    missing = [(name, defn) for name, defn in pending if name not in existing]

    if not missing:
        return

    with engine.begin() as conn:
        for col_name, col_def in missing:
            logger.info("Schema migration: ALTER TABLE users ADD COLUMN %s %s", col_name, col_def)
            conn.execute(text(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}"))

    logger.info("Schema migration complete: added %d column(s) to users.", len(missing))


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
