"""
Subscription state helpers (PostgreSQL + Redis).

Keeps User.is_premium / subscription_type / subscription_expires_at in sync
with the legacy plan / plan_expiry columns used by recipe quota, then mirrors
the result into Redis so Flutter status reads stay cheap.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime

import redis
from sqlalchemy.orm import Session

from models import User

logger = logging.getLogger(__name__)

# Same Redis instance as recipe cache; sync client matches billing/auth routers.
_redis_url = os.getenv("REDIS_URL")
if _redis_url:
    _redis = redis.from_url(_redis_url, decode_responses=True)
else:
    _redis = redis.Redis(host="localhost", port=6379, decode_responses=True)

SUBSCRIPTION_CACHE_TTL = 60 * 15  # 15 minutes


def subscription_cache_key(user_id: int) -> str:
    """Redis key for a user's live subscription snapshot."""
    return f"user:{user_id}:subscription"


def subscription_type_from_product(product_id: str | None) -> str:
    """Map a Play Store product id to monthly | yearly (default monthly)."""
    pid = (product_id or "").strip().lower()
    if "year" in pid or "annual" in pid or "anual" in pid:
        return "yearly"
    return "monthly"


def _snapshot(user: User) -> dict:
    """JSON-serialisable subscription view stored in Redis and returned by status."""
    expires = user.subscription_expires_at or user.plan_expiry
    return {
        "is_premium": bool(user.is_premium),
        "subscription_type": (user.subscription_type or "free").strip().lower() or "free",
        "subscription_expires_at": expires.isoformat() if expires else None,
        "plan": user.plan,
        "plan_expiry": user.plan_expiry.isoformat() if user.plan_expiry else None,
    }


def cache_user_subscription(user: User) -> None:
    """Write the current subscription snapshot to Redis. Failures are non-fatal."""
    try:
        _redis.setex(
            subscription_cache_key(user.id),
            SUBSCRIPTION_CACHE_TTL,
            json.dumps(_snapshot(user)),
        )
    except Exception as exc:
        logger.warning("Redis subscription cache write failed for user %s: %s", user.id, exc)


def apply_paid_subscription(
    user: User,
    *,
    subscription_type: str,
    expires_at: datetime,
) -> None:
    """
    Activate Premium on the ORM instance (caller commits).

    Also mirrors into plan / plan_expiry so generate-recipes quota is unchanged.
    """
    kind = (subscription_type or "monthly").strip().lower()
    if kind not in ("monthly", "yearly"):
        kind = "monthly"
    user.is_premium = True
    user.subscription_type = kind
    user.subscription_expires_at = expires_at
    user.plan = "premium"
    user.plan_expiry = expires_at


def apply_free_plan(user: User) -> None:
    """Downgrade the ORM instance to Free (caller commits)."""
    user.is_premium = False
    user.subscription_type = "free"
    user.subscription_expires_at = None
    user.plan = "free"
    user.plan_expiry = None


def sync_subscription_with_server_time(user: User, db: Session) -> dict:
    """
    Reconcile Premium against datetime.utcnow() and persist + cache the result.

    A paid period is active when subscription_type is monthly/yearly (or legacy
    plan == premium) and subscription_expires_at / plan_expiry is still in the
    future. Expired rows are written back to PostgreSQL as Free.
    """
    now = datetime.utcnow()
    sub_type = (user.subscription_type or "").strip().lower()
    expires = user.subscription_expires_at or user.plan_expiry
    legacy_premium = (user.plan or "").strip().lower() == "premium"

    paid_kind = sub_type in ("monthly", "yearly")
    if not paid_kind and legacy_premium:
        paid_kind = True
        if sub_type not in ("monthly", "yearly"):
            user.subscription_type = "monthly"

    still_active = False
    if paid_kind:
        if expires is None:
            still_active = True
        else:
            still_active = expires > now

    if still_active:
        dirty = False
        if not user.is_premium:
            user.is_premium = True
            dirty = True
        if (user.plan or "").strip().lower() != "premium":
            user.plan = "premium"
            dirty = True
        if (user.subscription_type or "").strip().lower() not in ("monthly", "yearly"):
            user.subscription_type = "monthly"
            dirty = True
        if expires is not None and user.subscription_expires_at != expires:
            user.subscription_expires_at = expires
            user.plan_expiry = expires
            dirty = True
        if dirty:
            db.commit()
            db.refresh(user)
    else:
        if (
            user.is_premium
            or (user.plan or "").strip().lower() == "premium"
            or (user.subscription_type or "free") != "free"
        ):
            apply_free_plan(user)
            db.commit()
            db.refresh(user)

    cache_user_subscription(user)
    return _snapshot(user)
