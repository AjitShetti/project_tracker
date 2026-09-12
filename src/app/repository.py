"""DynamoDB repository layer.

All direct boto3 interactions are isolated here.
Implements the 5 core access patterns using GetItem and Query exclusively.
No Scan operations are used anywhere.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from app.keys import (
    gsi1_pk,
    gsi1_sk,
    gsi1_sk_prefix,
    log_sk,
    log_sk_prefix,
    project_pk,
    project_sk,
)


def _to_iso(dt: datetime | str | None) -> str:
    """Format datetime or string into ISO-8601 UTC string."""
    if dt is None:
        return datetime.now(UTC).isoformat()
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.isoformat()
    return str(dt)


def _decimal_to_python(val: Any) -> Any:
    """Recursively convert Decimal instances to float or int for JSON serialization."""
    if isinstance(val, Decimal):
        return float(val) if "." in str(val) else int(val)
    if isinstance(val, list):
        return [_decimal_to_python(item) for item in val]
    if isinstance(val, dict):
        return {k: _decimal_to_python(v) for k, v in val.items()}
    return val


def _python_to_decimal(val: Any) -> Any:
    """Recursively convert float instances to Decimal for DynamoDB writes."""
    if isinstance(val, float):
        return Decimal(str(val))
    if isinstance(val, list):
        return [_python_to_decimal(item) for item in val]
    if isinstance(val, dict):
        return {k: _python_to_decimal(v) for k, v in val.items()}
    return val


def _query_all(table: Any, **query_kwargs: Any) -> list[dict[str, Any]]:
    """Run a Query and follow LastEvaluatedKey until every page is drained.

    DynamoDB caps a single Query response at 1 MB, so any query that can match
    an unbounded number of items must paginate or it will silently truncate.
    """
    items: list[dict[str, Any]] = []
    while True:
        response = table.query(**query_kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return items
        query_kwargs["ExclusiveStartKey"] = last_key


MAX_ID_ATTEMPTS = 5


def create_project(table: Any, payload_data: dict[str, Any]) -> dict[str, Any]:
    """Create a new project item in DynamoDB.

    project_id is a truncated UUID, so collisions are unlikely but possible. The
    conditional write makes a collision fail loudly instead of silently
    overwriting an existing project, and we simply try a fresh id.
    """
    now_iso = _to_iso(datetime.now(UTC))

    status_val = payload_data.get("status")
    status_str = status_val.value if hasattr(status_val, "value") else str(status_val)

    for _ in range(MAX_ID_ATTEMPTS):
        project_id = str(uuid.uuid4())[:8]
        item = {
            "PK": project_pk(project_id),
            "SK": project_sk(),
            "GSI1PK": gsi1_pk(),
            "GSI1SK": gsi1_sk(status_str, now_iso),
            "project_id": project_id,
            "name": payload_data["name"],
            "description": payload_data.get("description"),
            "status": status_str,
            "tech_stack": payload_data.get("tech_stack", []),
            "repo_url": payload_data.get("repo_url"),
            "live_url": payload_data.get("live_url"),
            "created_at": now_iso,
            "updated_at": now_iso,
        }

        # Filter out None values for optional attributes to keep DynamoDB clean
        clean_item = {k: v for k, v in item.items() if v is not None}
        try:
            table.put_item(
                Item=_python_to_decimal(clean_item),
                ConditionExpression=Attr("PK").not_exists() & Attr("SK").not_exists(),
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            continue  # id already taken - generate another one
        return _decimal_to_python(clean_item)

    raise RuntimeError(f"Could not allocate a unique project_id in {MAX_ID_ATTEMPTS} attempts")


def get_project(table: Any, project_id: str) -> dict[str, Any] | None:
    """Access Pattern 2: Get one project's metadata only (GetItem)."""
    response = table.get_item(
        Key={
            "PK": project_pk(project_id),
            "SK": project_sk(),
        }
    )
    item = response.get("Item")
    if not item:
        return None
    return _decimal_to_python(item)


def get_project_with_logs(table: Any, project_id: str) -> dict[str, Any] | None:
    """Access Pattern 1: Get one project with all its log entries in one Query."""
    items = _query_all(table, KeyConditionExpression=Key("PK").eq(project_pk(project_id)))
    if not items:
        return None

    project_item: dict[str, Any] | None = None
    logs: list[dict[str, Any]] = []

    for raw_item in items:
        item = _decimal_to_python(raw_item)
        sk = item.get("SK", "")
        if sk == project_sk():
            project_item = item
        elif sk.startswith(log_sk_prefix()):
            logs.append(item)

    if not project_item:
        return None

    # Reverse sort logs by created_at (newest first)
    logs.sort(key=lambda x: str(x.get("created_at", "")), reverse=True)
    project_item["logs"] = logs
    return project_item


def list_projects(table: Any, status_filter: str | None = None) -> list[dict[str, Any]]:
    """Access Patterns 4 & 5: List projects using GSI1.

    Pattern 4: Query GSI1PK = 'PROJECT'
    Pattern 5: Query GSI1PK = 'PROJECT' and GSI1SK begins_with '<status>#'
    """
    gsi1_pk_val = gsi1_pk()

    if status_filter:
        prefix = gsi1_sk_prefix(status_filter)
        key_condition = Key("GSI1PK").eq(gsi1_pk_val) & Key("GSI1SK").begins_with(prefix)
    else:
        key_condition = Key("GSI1PK").eq(gsi1_pk_val)

    items = _query_all(table, IndexName="GSI1", KeyConditionExpression=key_condition)
    return [_decimal_to_python(item) for item in items]


def update_project(
    table: Any, project_id: str, update_data: dict[str, Any]
) -> dict[str, Any] | None:
    """Partial update of a project item."""
    existing = get_project(table, project_id)
    if not existing:
        return None

    # Work on a copy - the caller owns the dict it passed in.
    changes = dict(update_data)

    now_iso = _to_iso(datetime.now(UTC))
    new_status = changes.get("status", existing["status"])
    if hasattr(new_status, "value"):
        new_status = new_status.value

    # Always refresh updated_at, and GSI1SK with it, since the GSI sort key
    # encodes status#updated_at and would otherwise drift out of sync.
    changes["updated_at"] = now_iso
    changes["status"] = new_status
    changes["GSI1SK"] = gsi1_sk(new_status, now_iso)

    set_clauses: list[str] = []
    remove_clauses: list[str] = []
    expr_names: dict[str, str] = {}
    expr_values: dict[str, Any] = {}

    for k, v in changes.items():
        placeholder_name = f"#{k}"
        expr_names[placeholder_name] = k
        if v is None:
            # An explicit null means "clear this optional attribute". REMOVE
            # deletes it outright rather than storing a DynamoDB NULL.
            remove_clauses.append(placeholder_name)
            continue
        placeholder_val = f":{k}"
        set_clauses.append(f"{placeholder_name} = {placeholder_val}")
        expr_values[placeholder_val] = v

    expression_parts: list[str] = []
    if set_clauses:
        expression_parts.append("SET " + ", ".join(set_clauses))
    if remove_clauses:
        expression_parts.append("REMOVE " + ", ".join(remove_clauses))

    update_kwargs: dict[str, Any] = {
        "Key": {
            "PK": project_pk(project_id),
            "SK": project_sk(),
        },
        "UpdateExpression": " ".join(expression_parts),
        "ExpressionAttributeNames": expr_names,
        # Refuse to resurrect a project deleted between the read above and this
        # write; without it update_item would happily create a partial item.
        "ConditionExpression": Attr("PK").exists(),
        "ReturnValues": "ALL_NEW",
    }
    if expr_values:
        update_kwargs["ExpressionAttributeValues"] = _python_to_decimal(expr_values)

    try:
        response = table.update_item(**update_kwargs)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return None
        raise
    return _decimal_to_python(response.get("Attributes", {}))


def delete_project(table: Any, project_id: str) -> bool:
    """Cascade delete project and all its log entries."""
    pk_val = project_pk(project_id)
    items = _query_all(
        table,
        KeyConditionExpression=Key("PK").eq(pk_val),
        ProjectionExpression="PK, SK",
    )
    if not items:
        return False

    with table.batch_writer() as batch:
        for item in items:
            batch.delete_item(
                Key={
                    "PK": item["PK"],
                    "SK": item["SK"],
                }
            )
    return True


def create_log(
    table: Any,
    project_id: str,
    note: str,
    hours_spent: float,
    created_at: datetime | str | None = None,
) -> dict[str, Any] | None:
    """Append a log entry to a project."""
    # Ensure project exists
    project = get_project(table, project_id)
    if not project:
        return None

    log_id = str(uuid.uuid4())[:8]
    created_at_iso = _to_iso(created_at)
    sk_val = log_sk(created_at_iso, log_id)

    log_item = {
        "PK": project_pk(project_id),
        "SK": sk_val,
        "log_id": log_id,
        "project_id": project_id,
        "note": note,
        "hours_spent": Decimal(str(hours_spent)),
        "created_at": created_at_iso,
    }

    table.put_item(Item=log_item)
    return _decimal_to_python(log_item)


def list_logs(table: Any, project_id: str) -> list[dict[str, Any]] | None:
    """Access Pattern 3: List a project's log entries, newest first."""
    # Ensure project exists
    project = get_project(table, project_id)
    if not project:
        return None

    pk_val = project_pk(project_id)
    prefix = log_sk_prefix()

    items = _query_all(
        table,
        KeyConditionExpression=Key("PK").eq(pk_val) & Key("SK").begins_with(prefix),
        ScanIndexForward=False,  # descending order -> newest first!
    )
    return [_decimal_to_python(item) for item in items]
