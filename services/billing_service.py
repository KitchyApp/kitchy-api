"""
Billing Service (Google Play)

Responsible for:
- sandbox/mock bypass during development (no Play Store account needed)
- validating real purchases with Google Play API
- preventing duplicate-token fraud
- returning structured results for the router to persist

NOTE: user.plan / user.plan_expiry are updated by the ROUTER, not here.
      This keeps the service layer free of ORM side-effects and testable
      without a live database session.
"""

from datetime import datetime, timedelta
from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import Purchase
from core.security import hash_token

# Google API imports — only used for real purchases
from google.oauth2 import service_account
from googleapiclient.discovery import build


# ========================
# CONFIGURATION
# ========================

PACKAGE_NAME = "com.kitchy.app"
SCOPES = ["https://www.googleapis.com/auth/androidpublisher"]
SERVICE_ACCOUNT_FILE = "google-service-account.json"

# ── Sandbox bypass ───────────────────────────────────────────────────────────
# Hardcoded token accepted during development / CI when no Google Play
# Developer account exists yet.
# TODO: remove (or gate behind an ENV flag) before publishing to the Play Store.
SANDBOX_TEST_TOKEN = "SANDBOX_TEST_TOKEN_V1"
SANDBOX_EXPIRY_DAYS = 30


# ========================
# GOOGLE CLIENT
# ========================

def get_google_client():
    """Builds an authenticated Google Play API client from a service account."""
    credentials = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=SCOPES,
    )
    return build("androidpublisher", "v3", credentials=credentials)


# ========================
# GOOGLE VALIDATION
# ========================

def verify_with_google(purchase_token: str, product_id: str) -> dict:
    """
    Calls the Google Play Developer API to validate a subscription purchase.

    Returns the raw API response dict.
    Raises HTTP 400 on any Google API error.
    """
    client = get_google_client()

    try:
        return (
            client.purchases()
            .subscriptions()
            .get(
                packageName=PACKAGE_NAME,
                subscriptionId=product_id,
                token=purchase_token,
            )
            .execute()
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Google API error: {exc}") from exc


# ========================
# PROCESS PURCHASE
# ========================

def process_purchase(
    db: Session,
    user,
    purchase_token: str,
    product_id: str,
) -> dict:
    """
    Purchase processing pipeline.

    Returns a dict that the caller (router) uses to update the user record:
        {
            "status":       "premium_activated",
            "expiry_date":  datetime,          # UTC datetime for user.plan_expiry
            "expires_in_days": int,
            "sandbox":      bool,
        }

    Raises HTTPException on any validation failure.

    NOTE: this function intentionally does NOT mutate user.plan or
    user.plan_expiry — that is the router's responsibility.
    """

    # ── SANDBOX BYPASS ───────────────────────────────────────────────────────
    # When the token is the reserved sandbox value we skip the Google Play API
    # call entirely.  No Purchase row is stored so the same sandbox token can
    # be used multiple times during development.
    if purchase_token == SANDBOX_TEST_TOKEN:
        expiry_date = datetime.utcnow() + timedelta(days=SANDBOX_EXPIRY_DAYS)
        return {
            "status": "premium_activated",
            "expiry_date": expiry_date,
            "expires_in_days": SANDBOX_EXPIRY_DAYS,
            "sandbox": True,
        }

    # ── REAL PURCHASE FLOW ───────────────────────────────────────────────────

    # 1. Prevent duplicate-token reuse (anti-fraud)
    token_hash = hash_token(purchase_token)

    if db.query(Purchase).filter(Purchase.purchase_token_hash == token_hash).first():
        raise HTTPException(status_code=400, detail="Purchase token already used")

    # 2. Validate with Google Play API
    data = verify_with_google(purchase_token, product_id)

    purchase_state = data.get("purchaseState")
    expiry_ms = data.get("expiryTimeMillis")

    # purchaseState 0 = active, 1 = cancelled, 2 = pending
    if purchase_state != 0:
        raise HTTPException(status_code=400, detail="Payment not completed or cancelled")

    if not expiry_ms:
        raise HTTPException(status_code=400, detail="Missing expiry in Google response")

    expiry_date = datetime.utcfromtimestamp(int(expiry_ms) / 1000)

    if expiry_date < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Subscription already expired")

    # 3. Persist the purchase record (duplicate-reuse guard for future requests)
    purchase = Purchase(
        user_id=user.id,
        product_id=product_id,
        purchase_token_hash=token_hash,
        expiry_date=expiry_date,
    )
    db.add(purchase)
    # flush so the row is written within this transaction, but let the
    # router call db.commit() after it has also updated user.plan
    db.flush()

    return {
        "status": "premium_activated",
        "expiry_date": expiry_date,
        "expires_in_days": (expiry_date - datetime.utcnow()).days,
        "sandbox": False,
    }
