import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

# ========================
# LOAD ENV
# ========================

load_dotenv()

# ========================
# DATABASE CONFIGURATION
# ========================

DATABASE_URL = os.getenv("DATABASE_URL")

# fallback para desenvolvimento local
if not DATABASE_URL:
    DATABASE_URL = "sqlite:///./app.db"


# ========================
# ENGINE SETUP
# ========================

# SQLAlchemy engine (core connection to the database)
# Detecta se é SQLite (precisa config especial)
is_sqlite = DATABASE_URL.startswith("sqlite")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if is_sqlite else {}
)


# ========================
# SESSION FACTORY
# ========================

# SessionLocal is a factory that creates new DB sessions
# Each request will get its own session instance
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,  # disables automatic flush (better control)
    autocommit=False  # explicit commit required (safer transactions)
)

# ========================
# BASE MODEL CLASS
# ========================

# Base class for all ORM models
# All database tables must inherit from this
Base = declarative_base()


# ========================
# DEPENDENCY (FASTAPI)
# ========================

def get_db():
    """
        FastAPI dependency that provides a database session.

        Lifecycle:
        - Opens a new DB session per request
        - Yields the session to the endpoint
        - Ensures the session is always closed (even on error)

        Usage:
            db: Session = Depends(get_db)
    """

    db = SessionLocal()
    try:
        yield db  # Provide DB session to route
    finally:
        db.close()  # Always close session (prevents connection leaks)
