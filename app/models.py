from datetime import datetime
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field

class ProjectStatus(str, Enum):
    IDEA = 'idea'
    ACTIVE = 'active'
    PAUSED = 'paused'
    SHIPPED = 'shipped'
    ABANDONED = 'abandoned'

class LogCreate(BaseModel):
    "Schema for appending a log entry"

    note: str = Field(..., min_length=1, max_length=2000, description="What was worked on")
    hours_spend: float = Field(..., ge=0.0, description="Hours spent during this interval")
    created_at: datetime | None = Field(
        default=None,
        description= "Optional ISO timestamp; defaults tp current time "
    )

class Logout(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    log_id: str
    project_id: str
    note: str
    hours_spent: float
    created_at: datetime


class ProjectCreate(BaseModel):

    name: str = Field(..., min_length=1, max_length=1000, description="Project name")
    description: str | None = Field(default=None, max_length=1000, description="Summary of what the project does")
    status: ProjectStatus = Field(
        default=ProjectStatus.IDEA,
        description="Current state of the project"
    )
    tech_stack: list[str] = Field(
        default_factory=list,
        description="Technologies used"
    )
    repo_url: str | None = Field(default=None, max_length=500, description="repo link") 
    live_url: str | None = Field(default=None, max_length=500, description="")

class ProjectUpdate(BaseModel):

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    status: ProjectStatus | None = None
    tech_stack: list[str] | None = None
    repo_url: str | None = Field(default=None, max_length=500)
    live_url: str | None = Field(default=None, max_length=500)



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
    logs: list[Logout] = Field(default_factory=list)
