from pydantic import BaseModel, EmailStr, Field

# bcrypt only hashes the first 72 bytes (bcrypt>=5 raises beyond that);
# hash_password() also enforces the byte limit for multi-byte characters.
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 72


class LoginRequest(BaseModel):
    # Plain str: login only looks the address up. EmailStr rejects reserved
    # domains such as .local, which locked out admin@hyperscope.local.
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    agent_id: int
    name: str
    email: str
    role: str


class AgentCreate(BaseModel):
    email: EmailStr
    name: str
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)
    role: str = "agent"


class AgentOut(BaseModel):
    id: int
    email: str
    name: str
    role: str
    is_active: bool
    avatar_color: str

    model_config = {"from_attributes": True}
