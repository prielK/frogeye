from pydantic import BaseModel, ConfigDict, computed_field

from app.core.config import get_settings
from app.models.enums import Role


class LoginIn(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str
    role: Role

    @computed_field(  # type: ignore[prop-decorator]
        description="The app's time zone (settings TIMEZONE, an IANA name): the UI shows every "
        "date and time in it, so the day matches the briefing's and the budget's."
    )
    @property
    def timezone(self) -> str:
        return get_settings().timezone


class PersonaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    email: str
    role: Role
