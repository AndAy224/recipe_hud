from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class SiteIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: HttpUrl
    color: str = "#e07a5f"
    icon: str = ""
    open_mode: Literal["direct", "clean"] = "direct"


class SiteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    url: HttpUrl | None = None
    color: str | None = None
    icon: str | None = None
    open_mode: Literal["direct", "clean"] | None = None


class ReorderBody(BaseModel):
    ids: list[int]


class TimerCreate(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    seconds: int = Field(gt=0, le=48 * 3600)


class ExtendBody(BaseModel):
    seconds: int = Field(gt=0, le=3600)


class PresetIn(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    seconds: int = Field(gt=0, le=48 * 3600)


class NavigateBody(BaseModel):
    url: HttpUrl


class PasswordBody(BaseModel):
    password: str = Field(min_length=4, max_length=128)
