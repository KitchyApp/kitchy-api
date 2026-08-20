"""
Billing Router

POST /billing/verify-purchase
    - Protected by JWT (get_current_user dependency)
    - Delegates validation logic to services/billing_service.py
    - Persists is_premium, subscription_type, subscription_expires_at

POST /billing/webhook
    - Provider callback: upgrades/downgrades the user in PostgreSQL + Redis
"""

import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import User
from schemas.billing import BillingWebhookPayload, PurchaseRequest
from services.billing_service import process_purchase
from services.analytics_service import log_analytics_event
from services.subscription_service import (
    apply_free_plan,
    apply_paid_subscription,
    cache_user_subscription,
    subscription_type_from_product,
)
from dependencies.auth import get_current_user

router = APIRouter()


def _activate_premium(user: User, product_id: str | None, expires_at: datetime) -> None:
    """Write paid fields on the user row (caller commits)."""
    apply_paid_subscription(
        user,
        subscription_type=subscription_type_from_product(product_id),
        expires_at=expires_at,
    )


@router.post("/verify-purchase")
def verify_purchase(
    data: PurchaseRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Validate a Google Play purchase (or sandbox token) and activate Premium.

    Flow:
    1. Call process_purchase() — handles sandbox bypass OR Google Play API
    2. If validation succeeds, persist plan upgrade on the user row
    3. Return a simple JSON success payload
    """
    result = process_purchase(
        db=db,
        user=current_user,
        purchase_token=data.purchase_token,
        product_id=data.product_id,
    )

    # process_purchase raises HTTPException on failure, so if we reach here
    # the purchase is valid. Update the user's plan in the same transaction.
    if result.get("status") != "premium_activated":
        # Defensive guard — should never happen given the service contract
        raise HTTPException(status_code=500, detail="Unexpected billing service response")

    _activate_premium(current_user, data.product_id, result["expiry_date"])

    db.commit()
    db.refresh(current_user)
    cache_user_subscription(current_user)

    # Record conversion event AFTER the user row is committed so the
    # analytics row reflects the final, persisted state.
    log_analytics_event(
        db,
        event_name="premium_converted",
        user_id=current_user.id,
        metadata={
            "product_id": data.product_id,
            "sandbox": result.get("sandbox", False),
            "expires_in_days": result["expires_in_days"],
        },
    )

    return {"status": "success", "message": "Premium activated"}


@router.post("/webhook")
def billing_webhook(
    data: BillingWebhookPayload,
    db: Session = Depends(get_db),
    x_webhook_secret: str | None = Header(default=None, alias="X-Webhook-Secret"),
):
    """
    Ingest a real subscription update from the payment provider.

    On payment.succeeded / subscription.renewed / status=active: set the user
    to Premium in PostgreSQL and refresh Redis.
    On cancelled / expired / payment_failed: revert to Free.
    """
    expected = os.getenv("BILLING_WEBHOOK_SECRET")
    if expected:
        if (x_webhook_secret or "") != expected:
            raise HTTPException(status_code=401, detail="Invalid webhook secret")

    user = None
    if data.user_id is not None:
        user = db.get(User, data.user_id)
    if user is None and data.email:
        user = db.query(User).filter(User.email == data.email.strip().lower()).first()
        if user is None:
            user = db.query(User).filter(User.email == data.email.strip()).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    event = (data.event_type or "").strip().lower()
    status = (data.status or "").strip().lower()
    success_events = {
        "payment.succeeded",
        "subscription.renewed",
        "subscription.purchased",
        "subscription.activated",
    }
    fail_statuses = {"cancelled", "canceled", "expired", "payment_failed", "revoked"}

    if event in success_events or status in {"active", "purchased", "renewed"}:
        kind = (data.subscription_type or "").strip().lower()
        if kind not in ("monthly", "yearly"):
            kind = subscription_type_from_product(data.product_id)
        expires = data.expires_at
        if expires is None:
            days = 365 if kind == "yearly" else 30
            expires = datetime.utcnow() + timedelta(days=days)
        apply_paid_subscription(user, subscription_type=kind, expires_at=expires)
        db.commit()
        db.refresh(user)
        cache_user_subscription(user)
        return {
            "status": "ok",
            "is_premium": True,
            "subscription_type": user.subscription_type,
            "subscription_expires_at": user.subscription_expires_at.isoformat()
            if user.subscription_expires_at
            else None,
        }

    if event in {"subscription.cancelled", "subscription.expired"} or status in fail_statuses:
        apply_free_plan(user)
        db.commit()
        db.refresh(user)
        cache_user_subscription(user)
        return {"status": "ok", "is_premium": False, "subscription_type": "free"}

    raise HTTPException(status_code=400, detail=f"Unhandled billing event: {data.event_type}")
