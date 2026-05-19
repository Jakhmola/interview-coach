import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class ResetAccountRequest(BaseModel):
    """Phase 22 — typed-email guard so a slip of the click can't nuke
    everything the user owns. FE renders an input next to the button and
    only enables submit when the value equals ``current_user.email``."""

    confirm_email: EmailStr
