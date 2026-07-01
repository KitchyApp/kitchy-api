"""
Service: analytics_service

Single entry-point for recording behavioural analytics events.

Design principles:
- NEVER raises — analytics must never interrupt the main request flow.
  If the DB write fails the exception is swallowed and logged.
- Always rolls back the session on failure so the caller's session
  stays in a clean, usable state after the call returns.
- Accepts an optional metadata dict; anything JSON-serialisable is valid.
- user_id is optional (nullable) to support future anonymous tracking.
"""

import json
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from models.analytics import AnalyticsEvent

logger = logging.getLogger(__name__)


def log_analytics_event(
    db: Session,
    event_name: str,
    user_id: Optional[int] = None,
    metadata: Optional[dict] = None,
) -> None:
    """
    Persist a single analytics event row.

    Args:
        db          SQLAlchemy session from the current request.
        event_name  Snake-case event identifier (e.g. "limit_blocked_403").
        user_id     Authenticated user's id, or None for anonymous events.
        metadata    Optional dict with event-specific context.
                    Stored as JSON; any JSON-serialisable value is accepted.

    Returns:
        None — callers must never depend on the return value.

    Example:
        log_analytics_event(
            db,
            event_name="limit_blocked_403",
            user_id=current_user.id,
            metadata={"plan": current_user.plan, "analyses_today": 1, "limit": 1},
        )
    """
    try:
        event = AnalyticsEvent(
            user_id=user_id,
            event_name=event_name,
            metadata_json=json.dumps(metadata or {}, ensure_ascii=False),
            created_at=datetime.utcnow(),
        )
        db.add(event)
        db.commit()

        logger.debug(
            "[analytics] Event logged: %s | user_id=%s",
            event_name,
            user_id,
        )

    except Exception as exc:
        # Roll back so the session remains usable for subsequent operations
        # (e.g. the caller still needs to commit its own changes).
        try:
            db.rollback()
        except Exception:
            pass

        logger.error(
            "[analytics] Failed to log event '%s' for user_id=%s: %s",
            event_name,
            user_id,
            exc,
        )
