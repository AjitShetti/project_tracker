"""Centralized DynamoDB key construction.

All partition keys, sort keys, and GSI keys are generated here to prevent key
inconsistencies across the application.
"""

PROJECT_PREFIX = "PROJECT#"
LOG_PREFIX = "LOG#"
METADATA_SK = "METADATA"
GSI1_PROJECT_PK = "PROJECT"


def project_pk(project_id: str) -> str:
    """Generate partition key for a project and its attached items."""
    return f"{PROJECT_PREFIX}{project_id}"


def project_sk() -> str:
    """Generate sort key for project metadata item."""
    return METADATA_SK


def gsi1_pk() -> str:
    """Generate partition key for GSI1 (groups all projects together)."""
    return GSI1_PROJECT_PK


def gsi1_sk(status: str, updated_at: str) -> str:
    """Generate sort key for GSI1 (enables status filtering & recency ordering)."""
    return f"{status}#{updated_at}"


def gsi1_sk_prefix(status: str) -> str:
    """Generate sort key prefix for querying GSI1 by a specific status."""
    return f"{status}#"


def log_sk(created_at: str, log_id: str) -> str:
    """Generate sort key for a log entry item (chronologically sortable)."""
    return f"{LOG_PREFIX}{created_at}#{log_id}"


def log_sk_prefix() -> str:
    """Generate sort key prefix for querying all logs of a project."""
    return LOG_PREFIX
