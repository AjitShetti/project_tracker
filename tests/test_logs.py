from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.keys import log_sk, project_pk


def test_create_log_success(client, auth_headers):
    """Append a valid log entry to an existing project."""
    project = client.post(
        "/api/v1/projects",
        json={"name": "Log Test Project", "status": "active"},
        headers=auth_headers,
    ).json()
    project_id = project["project_id"]

    log_payload = {
        "note": "Implemented authentication and DynamoDB single-table design",
        "hours_spent": 3.5,
    }
    resp = client.post(
        f"/api/v1/projects/{project_id}/logs",
        json=log_payload,
        headers=auth_headers,
    )
    assert resp.status_code == 201
    log = resp.json()
    assert "log_id" in log
    assert log["project_id"] == project_id
    assert log["note"] == log_payload["note"]
    assert log["hours_spent"] == 3.5
    assert log["created_at"] is not None


def test_create_log_project_not_found(client, auth_headers):
    """Appending a log entry to a non-existent project returns 404."""
    resp = client.post(
        "/api/v1/projects/non-existent-proj/logs",
        json={"note": "Some note", "hours_spent": 1.0},
        headers=auth_headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Project not found"


def test_list_logs_newest_first(client, auth_headers):
    """List log entries and verify they are returned sorted chronologically descending."""
    project = client.post(
        "/api/v1/projects",
        json={"name": "Ordering Project", "status": "active"},
        headers=auth_headers,
    ).json()
    project_id = project["project_id"]

    base_time = datetime.now(UTC)
    t1 = (base_time - timedelta(hours=3)).isoformat()
    t2 = (base_time - timedelta(hours=2)).isoformat()
    t3 = (base_time - timedelta(hours=1)).isoformat()

    client.post(
        f"/api/v1/projects/{project_id}/logs",
        json={"note": "Earliest entry", "hours_spent": 1.0, "created_at": t1},
        headers=auth_headers,
    )
    client.post(
        f"/api/v1/projects/{project_id}/logs",
        json={"note": "Middle entry", "hours_spent": 1.5, "created_at": t2},
        headers=auth_headers,
    )
    client.post(
        f"/api/v1/projects/{project_id}/logs",
        json={"note": "Latest entry", "hours_spent": 2.0, "created_at": t3},
        headers=auth_headers,
    )

    resp = client.get(f"/api/v1/projects/{project_id}/logs", headers=auth_headers)
    assert resp.status_code == 200
    logs = resp.json()
    assert len(logs) == 3

    # Check newest first
    assert logs[0]["note"] == "Latest entry"
    assert logs[1]["note"] == "Middle entry"
    assert logs[2]["note"] == "Earliest entry"


def test_get_project_with_logs_query(client, auth_headers):
    """Access Pattern 1: Get project with embedded logs in a single query."""
    project = client.post(
        "/api/v1/projects",
        json={"name": "Combined Query Project", "status": "active"},
        headers=auth_headers,
    ).json()
    project_id = project["project_id"]

    client.post(
        f"/api/v1/projects/{project_id}/logs",
        json={"note": "Task A completed", "hours_spent": 2.0},
        headers=auth_headers,
    )
    client.post(
        f"/api/v1/projects/{project_id}/logs",
        json={"note": "Task B completed", "hours_spent": 1.0},
        headers=auth_headers,
    )

    resp = client.get(
        f"/api/v1/projects/{project_id}?include_logs=true",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["project_id"] == project_id
    assert "logs" in data
    assert len(data["logs"]) == 2


def test_log_validation_errors(client, auth_headers):
    """Validation errors for invalid log payloads."""
    project = client.post(
        "/api/v1/projects",
        json={"name": "Validation Project", "status": "active"},
        headers=auth_headers,
    ).json()
    project_id = project["project_id"]

    # Negative hours
    resp_neg = client.post(
        f"/api/v1/projects/{project_id}/logs",
        json={"note": "Negative hours", "hours_spent": -1.0},
        headers=auth_headers,
    )
    assert resp_neg.status_code == 422

    # Empty note
    resp_empty = client.post(
        f"/api/v1/projects/{project_id}/logs",
        json={"note": "", "hours_spent": 1.0},
        headers=auth_headers,
    )
    assert resp_empty.status_code == 422


def test_create_log_without_hours_records_a_note(client, auth_headers):
    """ "I had another thought about this" has no hours attached to report."""
    project_id = client.post(
        "/api/v1/projects",
        json={"name": "Thoughts Project", "status": "active"},
        headers=auth_headers,
    ).json()["project_id"]

    resp = client.post(
        f"/api/v1/projects/{project_id}/logs",
        json={"note": "Idea: cache the GSI1 query per cold start"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["hours_spent"] == 0.0

    listed = client.get(f"/api/v1/projects/{project_id}/logs", headers=auth_headers)
    assert listed.status_code == 200
    assert listed.json()[0]["hours_spent"] == 0.0

    # The default must not have loosened the constraint it replaced.
    negative = client.post(
        f"/api/v1/projects/{project_id}/logs",
        json={"note": "Impossible", "hours_spent": -0.5},
        headers=auth_headers,
    )
    assert negative.status_code == 422


def test_log_written_before_hours_became_optional_still_reads(dynamodb_mock, client, auth_headers):
    """Logs stored while hours_spent was required must still deserialise."""
    project_id = client.post(
        "/api/v1/projects",
        json={"name": "Has history", "status": "active"},
        headers=auth_headers,
    ).json()["project_id"]

    created_at = "2026-09-01T09:00:00+00:00"
    dynamodb_mock.put_item(
        Item={
            "PK": project_pk(project_id),
            "SK": log_sk(created_at, "old00001"),
            "log_id": "old00001",
            "project_id": project_id,
            "note": "Written under the old required-hours schema",
            "hours_spent": Decimal("2.5"),
            "created_at": created_at,
        }
    )

    listed = client.get(f"/api/v1/projects/{project_id}/logs", headers=auth_headers)
    assert listed.status_code == 200
    assert listed.json()[0]["log_id"] == "old00001"
    assert listed.json()[0]["hours_spent"] == 2.5
