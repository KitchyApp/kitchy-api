"""
Billing Service (Google Play)

Responsible for:
- validating purchases with Google API
- preventing fraud (duplicate tokens)
- activating premium subscriptions
"""

from datetime import datetime
from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import Purchase
from core.security import hash_token

# Google API imports
from google.oauth2 import service_account
from googleapiclient.discovery import build


# ========================
# CONFIGURATION
# ========================

PACKAGE_NAME = "com.kitchy.app"
SCOPES = ["https://www.googleapis.com/auth/androidpublisher"]
SERVICE_ACCOUNT_FILE = "google-service-account.json"


# ========================
# GOOGLE CLIENT
# ========================

def get_google_client():
    """
    Creates authenticated Google Play API client.
    """
    credentials = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=SCOPES
    )

    return build("androidpublisher", "v3", credentials=credentials)


# ========================
# VERIFY PURCHASE
# ========================

def verify_with_google(purchase_token: str, product_id: str):
    """
    Calls Google Play API to validate subscription.
    """
    client = get_google_client()

    try:
        response = client.purchases().subscriptions().get(
            packageName=PACKAGE_NAME,
            subscriptionId=product_id,
            token=purchase_token
        ).execute()

        return response

    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Google API error: {str(e)}"
        )


# ========================
# PROCESS PURCHASE
# ========================

def process_purchase(db: Session, user, purchase_token: str, product_id: str):
    """
    Full purchase processing pipeline.

    Steps:
    1. Prevent duplicate token usage
    2. Validate with Google API
    3. Check payment state
    4. Check expiry
    5. Store purchase
    6. Activate premium
    """

    token_hash = hash_token(purchase_token)

    # Prevent reuse
    existing = db.query(Purchase).filter(
        Purchase.purchase_token_hash == token_hash
    ).first()

    if existing:
        raise HTTPException(status_code=400, detail="Purchase already used")

    # Google validation
    data = verify_with_google(purchase_token, product_id)

    purchase_state = data.get("purchaseState")
    expiry_ms = data.get("expiryTimeMillis")

    if purchase_state != 0:
        raise HTTPException(status_code=400, detail="Payment not completed")

    expiry_date = datetime.utcfromtimestamp(int(expiry_ms) / 1000)

    if expiry_date < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Subscription expired")

    # Persist purchase
    purchase = Purchase(
        user_id=user.id,
        product_id=product_id,
        purchase_token_hash=token_hash,
        expiry_date=expiry_date
    )

    db.add(purchase)

    #  Activate premium
    user.plan = "premium"
    user.plan_expiry = expiry_date

    db.commit()

    return {
        "status": "premium_activated",
        "expiry": expiry_date.isoformat()
    }
