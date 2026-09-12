from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app import repository
from app.deps import get_table
from app.models import LogCreate, LogOut

router = APIRouter(prefix="/projects", tags=["logs"])


@router.post(
    "/{project_id}/logs",
    response_model=LogOut,
    status_code=status.HTTP_201_CREATED,
    summary="Append a log entry",
)
def create_log(
    project_id: str,
    payload: LogCreate,
    table: Any = Depends(get_table),
):
    log_data = repository.create_log(
        table=table,
        project_id=project_id,
        note=payload.note,
        hours_spent=payload.hours_spent,
        created_at=payload.created_at,
    )
    if not log_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    return log_data


@router.get(
    "/{project_id}/logs",
    response_model=list[LogOut],
    summary="List log entries (newest first)",
)
def list_logs(
    project_id: str,
    table: Any = Depends(get_table),
):
    logs = repository.list_logs(table=table, project_id=project_id)
    if logs is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    return logs
