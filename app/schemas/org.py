from pydantic import BaseModel, EmailStr, Field, field_validator


class OrgOut(BaseModel):
    uid: str
    name: str
    support_email: str | None = None
    support_url: str | None = None

    model_config = {"from_attributes": True}


class OrgUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    support_email: EmailStr | None = None
    support_url: str | None = Field(None, max_length=500)

    @field_validator("support_email", mode="before")
    @classmethod
    def _blank_email_is_none(cls, v):
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("Workspace name cannot be empty")
        return v

    @field_validator("support_url")
    @classmethod
    def _http_url(cls, v: str | None) -> str | None:
        if v in (None, ""):
            return None
        if not v.startswith(("https://", "http://")):
            raise ValueError("Support URL must start with http:// or https://")
        return v
