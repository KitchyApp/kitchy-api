"""
Billing Router

POST /billing/verify-purchase
    - Protected by JWT (get_current_user dependency)
    - Delegates validation logic to services/billing_service.py
    - Owns the user-plan DB update: plan, plan_expiry, commit, refresh
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import User
from schemas.billing import PurchaseRequest
from services.billing_service import process_purchase
from services.analytics_service import log_analytics_event
from dependencies.auth import get_current_user

router = APIRouter()


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
    3. Return {"status": "premium_activated"}

    The service layer is side-effect-free regarding user.plan; all DB writes
    to the user happen here so they are easy to audit and roll back together.
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

    current_user.plan = "premium"
    current_user.plan_expiry = result["expiry_date"]

    db.commit()
    db.refresh(current_user)

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

    return {
        "status": "premium_activated",
        "expires_in_days": result["expires_in_days"],
        "sandbox": result.get("sandbox", False),
    }

