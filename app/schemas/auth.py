from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


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
    password: str
    role: str = "agent"


class AgentOut(BaseModel):
    id: int
    email: str
    name: str
    role: str
    is_active: bool
    avatar_color: str

    model_config = {"from_attributes": True}
