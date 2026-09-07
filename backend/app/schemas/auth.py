from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LoginResponse(BaseModel):
    token: str
    expires_in_seconds: int = Field(default=14 * 86_400)


class MeTenant(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    id: UUID
    slug: str
    name: str
    role: str
    status: str
    plan_code: str = Field(serialization_alias="planCode")
    trial_ends_at: str | None = Field(default=None, serialization_alias="trialEndsAt")
    # The shop's own currency, so every screen can label money the same way the
    # generated contract and receipt PDFs do. It lives on TenantSettings, but it
    # is carried HERE because /auth/me is the one payload every authenticated
    # surface already has: sourcing it from the settings bundle instead would put
    # a second request behind every money label on every page. Defaults to UZS
    # for a tenant with no settings row yet.
    currency: str = Field(default="UZS", max_length=8)


class MeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    id: UUID
    telegram_id: int = Field(serialization_alias="telegramId")
    telegram_username: str | None = Field(default=None, serialization_alias="telegramUsername")
    first_name: str | None = Field(default=None, serialization_alias="firstName")
    last_name: str | None = Field(default=None, serialization_alias="lastName")
    #: The RESOLVED name to address this person by — already decided by
    #: `services.display_names.person_name`, so no caller re-derives the rule and
    #: no surface can accidentally greet someone by an operator's name. None
    #: means "no personal name is known"; render a neutral greeting.
    display_name: str | None = Field(default=None, serialization_alias="displayName")
    photo_url: str | None = Field(default=None, serialization_alias="photoUrl")
    phone: str | None = None
    email: str | None = None
    birthdate: str | None = None
    gender: str | None = None
    locale: str
    platform_role: str | None = Field(default=None, serialization_alias="platformRole")
    tenants: list[MeTenant] = Field(default_factory=list)
