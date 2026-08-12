"""Pydantic request/response schemas for authentication endpoints."""

from pydantic import BaseModel, EmailStr, constr


class LoginSchema(BaseModel):
    """Request body for POST /auth/login."""

    email: EmailStr  # Validated email address.
    password: constr(min_length=8)  # Plain-text password; min 8 characters.


class RegisterSchema(BaseModel):
    """Request body for POST /auth/register."""

    email: EmailStr  # Unique email used as the login identifier.
    password: constr(min_length=8)  # Plain-text password; hashed before storage.


class RefreshSchema(BaseModel):
    """Request body for POST /auth/refresh."""

    refresh_token: str  # Opaque refresh token issued at login.
