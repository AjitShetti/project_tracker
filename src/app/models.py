from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProjectStatus(StrEnum):
    IDEA = "idea"
    ACTIVE = "active"
    PAUSED = "paused"
    SHIPPED = "shipped"
    ABANDONED = "abandoned"


class LogCreate(BaseModel):
    """Schema for appending a log entry."""

    note: str = Field(..., min_length=1, max_length=2000, description="What was worked on")
    hours_spent: float = Field(..., ge=0.0, description="Hours spent during this interval")
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
