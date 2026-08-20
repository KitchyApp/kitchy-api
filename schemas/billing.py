"""Pydantic schemas for Google Play billing verification and webhooks."""

from datetime import datetime

from pydantic import BaseModel, Field


class PurchaseRequest(BaseModel):
    """
    Payload for POST /billing/verify-purchase.

    purchase_token: Google Play serverVerificationData, or the literal
                    string "SANDBOX_TEST_TOKEN_V1" for development/testing.
    product_id:     Play Store product ID (e.g. "premium_monthly") or any
                    non-empty string when using the sandbox token.
    """

    purchase_token: str = Field(
        ...,
        min_length=1,
        description=(
            "Google Play purchase token received from the client, "
            "or 'SANDBOX_TEST_TOKEN_V1' for sandbox/mock testing."
        ),
    )
    product_id: str = Field(
        ...,
        min_length=1,
        description="Play Store subscription product ID (e.g. 'premium_monthly').",
    )


class BillingWebhookPayload(BaseModel):
    """
    Payload for POST /billing/webhook (Play / payment provider callbacks).

    Identify the user with user_id and/or email. On a successful payment
    event the user is upgraded to Premium in PostgreSQL and Redis.
    """

    event_type: str = Field(
        default="payment.succeeded",
        description="e.g. payment.succeeded, subscription.renewed, subscription.cancelled",
    )
    user_id: int | None = Field(default=None, description="Internal user id")
    email: str | None = Field(default=None, description="Account email if user_id is omitted")
    product_id: str | None = Field(default=None, description="Play Store product id")
    subscription_type: str | None = Field(
        default=None,
        description="free | monthly | yearly — inferred from product_id when omitted",
    )
    status: str = Field(
        default="active",
        description="active | cancelled | expired | payment_failed",
    )
    expires_at: datetime | None = Field(
        default=None,
        description="UTC end of the paid period; defaults to +30d monthly / +365d yearly",
    )
