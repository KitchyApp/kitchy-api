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
