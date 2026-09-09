import uuid

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    # Accepts email, login, or login_key so clients are not forced to name the field 'email'
    email: str = Field(validation_alias=AliasChoices("email", "login", "login_key"))
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    is_superadmin: bool
