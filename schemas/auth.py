from pydantic import BaseModel, EmailStr, constr


# ========================
# AUTH SCHEMAS
# ========================

class LoginSchema(BaseModel):
    """
        Schema used for user login requests.

        Fields:
            email: User's email address
            password: User's plain-text password (will be validated against hashed version in backend)

        Notes:
        - This schema ensures request body validation via Pydantic
        - Automatically enforces required fields
        - Can be extended later with stricter validation (e.g., EmailStr, min length)
    """

    # EMAIL VALIDATION
    email: EmailStr  # ensures valid email format

    # PASSWORD VALIDATION
    password: constr(min_length=8)  # minimum 8 characters


class RefreshSchema(BaseModel):
    refresh_token: str
