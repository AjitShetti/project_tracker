from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProjectStatus(StrEnum):
    IDEA = "idea"
    ACTIVE = "active"
    PAUSED = "paused"
    SHIPPED = "shipped"
    ABANDONED = "abandoned"


# Words callers say, mapped onto the five states above. These are synonyms for
# input only - nothing here becomes a stored value, and ProjectStatus stays at
# five members. A voice client hears "under review" and "to build"; both mean
# idea. Keeping the vocabulary here rather than in each client means curl, a
# phone shortcut and the desktop app all accept the same words, and a new
# synonym is one line in one place.
_STATUS_SYNONYMS = {
    "to_build": "idea",
    "under_review": "idea",
    "someday": "idea",
    "maybe": "idea",
    "backlog": "idea",
    "planned": "idea",
    "building": "active",
    "in_progress": "active",
    "wip": "active",
    "started": "active",
    "parked": "paused",
    "shelved": "paused",
    "on_hold": "paused",
    "done": "shipped",
    "live": "shipped",
    "launched": "shipped",
    "complete": "shipped",
    "dead": "abandoned",
    "dropped": "abandoned",
    "killed": "abandoned",
}


def _normalise_status(value: object) -> object:
    """Accept the words people say for the five states we store.

    Only spelling is normalised here: case, spaces and hyphens are folded, then
    a synonym is swapped for its canonical state. An unrecognised word is handed
    back unchanged so enum validation still rejects it - this widens the
    accepted vocabulary, it does not turn status into free text.
    """
    if not isinstance(value, str):
        return value
    key = value.strip().lower().replace(" ", "_").replace("-", "_")
    return _STATUS_SYNONYMS.get(key, key)


class LogCreate(BaseModel):
    """Schema for appending a log entry."""

    note: str = Field(..., min_length=1, max_length=2000, description="What was worked on")
    hours_spent: float = Field(
        default=0.0,
        ge=0.0,
        description="Hours spent during this interval. Omit for a note that "
        "records a thought rather than time worked.",
    )
    created_at: datetime | None = Field(
        default=None,
        description="Optional ISO timestamp; defaults to current time",
    )


class LogOut(BaseModel):
    """Schema for returning a log entry."""

    model_config = ConfigDict(from_attributes=True)

    log_id: str
    project_id: str
    note: str
    hours_spent: float
    created_at: datetime


class ProjectCreate(BaseModel):
    """Schema for creating a new project."""

    name: str = Field(..., min_length=1, max_length=1000, description="Project name")
    description: str | None = Field(
        default=None, max_length=1000, description="Summary of what the project does"
    )
    status: ProjectStatus = Field(
        default=ProjectStatus.IDEA,
        description="Current state of the project",
    )
    tech_stack: list[str] = Field(
        default_factory=list,
        description="Technologies used",
    )
    repo_url: str | None = Field(default=None, max_length=500, description="Repository link")
    live_url: str | None = Field(default=None, max_length=500, description="Live deployment link")
    client_token: str | None = Field(
        default=None,
        max_length=64,
        description="Caller-generated id. Retrying with the same token returns "
        "the existing project instead of creating a second one.",
    )

    @field_validator("status", mode="before")
    @classmethod
    def _accept_synonyms(cls, value: object) -> object:
        # mode="before" so this runs ahead of enum coercion - by the time a
        # plain validator sees the value, "under review" has already 422'd.
        return _normalise_status(value)


class ProjectUpdate(BaseModel):
    """Schema for partial update of a project.

    Every field is optional so that an omitted field means "leave unchanged".
    For the optional attributes an explicit null additionally means "clear it",
    but name and status are required on a project, so an explicit null there is
    rejected rather than allowed through to produce an unreadable item.
    """

    name: str | None = Field(default=None, min_length=1, max_length=1000)
    description: str | None = Field(default=None, max_length=1000)
    status: ProjectStatus | None = None
    tech_stack: list[str] | None = None
    repo_url: str | None = Field(default=None, max_length=500)
    live_url: str | None = Field(default=None, max_length=500)

    @field_validator("status", mode="before")
    @classmethod
    def _accept_synonyms(cls, value: object) -> object:
        # mode="before" so this runs ahead of enum coercion - by the time a
        # plain validator sees the value, "done" has already 422'd. A null falls
        # straight through to _reject_explicit_null below, which still refuses
        # it: normalising the vocabulary must not make status clearable.
        return _normalise_status(value)

    @field_validator("name", "status", "tech_stack")
    @classmethod
    def _reject_explicit_null(cls, value: object) -> object:
        # Validators do not run on unset defaults, so this only fires when the
        # client actually sent `null` for one of these fields.
        if value is None:
            raise ValueError("field is required and may not be set to null")
        return value


class ProjectOut(BaseModel):
    """Schema for returning project metadata."""

    model_config = ConfigDict(from_attributes=True)

    project_id: str
    name: str
    description: str | None = None
    status: ProjectStatus
    tech_stack: list[str] = Field(default_factory=list)
    repo_url: str | None = None
    live_url: str | None = None
    created_at: datetime
    updated_at: datetime


class ProjectWithLogsOut(ProjectOut):
    """Schema for returning a project with all of its log entries."""

    logs: list[LogOut] = Field(default_factory=list)
