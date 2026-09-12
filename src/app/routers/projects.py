from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app import repository
from app.deps import get_table
from app.models import (
    ProjectCreate,
    ProjectOut,
    ProjectStatus,
    ProjectUpdate,
    ProjectWithLogsOut,
    _normalise_status,
)

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post(
    "",
    response_model=ProjectOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a project",
)
def create_project(
    payload: ProjectCreate,
    table: Any = Depends(get_table),
):
    return repository.create_project(table, payload.model_dump())


@router.get(
    "",
    response_model=list[ProjectOut],
    summary="List all projects (optional status filter)",
)
def list_projects(
    # Typed str, not ProjectStatus: FastAPI coerces a query parameter against
    # its annotation before any validator of ours could widen the vocabulary, so
    # "under review" would 422 here while being accepted on the body endpoints.
    status_filter: str | None = Query(
        default=None,
        alias="status",
        description="idea, active, paused, shipped or abandoned, or any accepted "
        "synonym for one of them (see _STATUS_SYNONYMS).",
    ),
    table: Any = Depends(get_table),
):
    filter_val = None
    if status_filter:
        try:
            filter_val = ProjectStatus(_normalise_status(status_filter)).value
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"unknown status: {status_filter}",
            ) from None
    return repository.list_projects(table, status_filter=filter_val)


@router.get(
    "/{project_id}",
    response_model=ProjectWithLogsOut | ProjectOut,
    summary="Get one project (optionally with logs)",
)
def get_project(
    project_id: str,
    include_logs: bool = Query(default=False),
    table: Any = Depends(get_table),
):
    if include_logs:
        project = repository.get_project_with_logs(table, project_id)
    else:
        project = repository.get_project(table, project_id)

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    return project


@router.patch(
    "/{project_id}",
    response_model=ProjectOut,
    summary="Partial update of a project",
)
def update_project(
    project_id: str,
    payload: ProjectUpdate,
    table: Any = Depends(get_table),
):
    update_data = payload.model_dump(exclude_unset=True)
    if not update_data:
        # If body is empty, just retrieve current project
        existing = repository.get_project(table, project_id)
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            )
        return existing

    updated = repository.update_project(table, project_id, update_data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    return updated


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete project and all its logs",
)
def delete_project(
    project_id: str,
    table: Any = Depends(get_table),
):
    deleted = repository.delete_project(table, project_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    return
