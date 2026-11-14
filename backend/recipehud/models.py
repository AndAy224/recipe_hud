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


class RecipeUrlBody(BaseModel):
    url: HttpUrl


class SendBody(BaseModel):
    url: HttpUrl
    clean: bool = False


class TagsBody(BaseModel):
    url: HttpUrl
    tags: list[str] = Field(max_length=10)


class RenameBody(BaseModel):
    url: HttpUrl
    title: str = Field(min_length=1, max_length=200)


class NotesBody(BaseModel):
    """Only the fields that are sent are changed; an empty note or a null/0
    rating clears that field."""
    url: HttpUrl
    notes: str | None = Field(default=None, max_length=2000)
    rating: int | None = Field(default=None, ge=0, le=5)


class CookedBody(BaseModel):
    url: HttpUrl
    undo: bool = False  # take back the most recent "cooked it"
