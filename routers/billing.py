from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from schemas.billing import PurchaseRequest
from services.billing_service import process_purchase
from dependencies.auth import get_current_user

router = APIRouter()


@router.post("/verify-purchase")
def verify_purchase(
    data: PurchaseRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    return process_purchase(
        db=db,
        user=current_user,
        purchase_token=data.purchase_token,
        product_id=data.product_id
    )

