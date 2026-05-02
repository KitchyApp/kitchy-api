from pydantic import BaseModel


class PurchaseRequest(BaseModel):
    purchase_token: str
    product_id: str
