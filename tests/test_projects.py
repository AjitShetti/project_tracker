def test_health_endpoints(client):
    """Health check endpoints must be accessible without authentication."""
    resp1 = client.get("/health")
    assert resp1.status_code == 200
    assert resp1.json() == {"status": "ok"}

    resp2 = client.get("/api/v1/health")
    assert resp2.status_code == 200
    assert resp2.json() == {"status": "ok"}


def test_api_key_authentication(client, auth_headers):
    """Protected endpoints require valid X-API-Key header."""
    # Missing header -> 401
    resp_missing = client.get("/api/v1/projects")
    assert resp_missing.status_code == 401
    assert "Invalid or missing API key" in resp_missing.json()["detail"]

    # Invalid header -> 401
    resp_invalid = client.get("/api/v1/projects", headers={"X-API-Key": "wrong-key"})
    assert resp_invalid.status_code == 401

    # Valid header -> 200
    resp_valid = client.get("/api/v1/projects", headers=auth_headers)
    assert resp_valid.status_code == 200
    assert resp_valid.json() == []


def test_create_and_get_project(client, auth_headers):
    """Create a project and retrieve its metadata."""
    payload = {
        "name": "Side Project Tracker",
        "description": "A tracker for side projects",
        "status": "active",
        "tech_stack": ["FastAPI", "DynamoDB", "AWS Lambda"],
        "repo_url": "https://github.com/example/tracker",
        "live_url": "https://api.example.com",
    }
    create_resp = client.post("/api/v1/projects", json=payload, headers=auth_headers)
    assert create_resp.status_code == 201
    data = create_resp.json()
    assert "project_id" in data
    assert data["name"] == payload["name"]
    assert data["status"] == "active"
    assert data["tech_stack"] == payload["tech_stack"]
    assert data["created_at"] is not None
    assert data["updated_at"] is not None

    project_id = data["project_id"]

    # Retrieve single project
    get_resp = client.get(f"/api/v1/projects/{project_id}", headers=auth_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["project_id"] == project_id
    assert get_resp.json()["name"] == payload["name"]


def test_get_project_not_found(client, auth_headers):
    """Attempting to get a non-existent project returns 404."""
    resp = client.get("/api/v1/projects/non-existent-id", headers=auth_headers)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Project not found"


def test_list_and_filter_projects(client, auth_headers):
    """List all projects and filter by status using GSI1."""
    # Create projects with different statuses
    p1 = client.post(
        "/api/v1/projects",
        json={"name": "Project Alpha", "status": "active"},
        headers=auth_headers,
    ).json()

    p2 = client.post(
        "/api/v1/projects",
        json={"name": "Project Beta", "status": "idea"},
        headers=auth_headers,
    ).json()

    p3 = client.post(
        "/api/v1/projects",
        json={"name": "Project Gamma", "status": "active"},
        headers=auth_headers,
    ).json()

    # List all
    all_resp = client.get("/api/v1/projects", headers=auth_headers)
    assert all_resp.status_code == 200
    all_ids = {p["project_id"] for p in all_resp.json()}
    assert {p1["project_id"], p2["project_id"], p3["project_id"]}.issubset(all_ids)

    # Filter by status = active
    active_resp = client.get("/api/v1/projects?status=active", headers=auth_headers)
    assert active_resp.status_code == 200
    active_projects = active_resp.json()
    assert len(active_projects) == 2
    assert all(p["status"] == "active" for p in active_projects)

    # Filter by status = idea
    idea_resp = client.get("/api/v1/projects?status=idea", headers=auth_headers)
    assert idea_resp.status_code == 200
    idea_projects = idea_resp.json()
    assert len(idea_projects) == 1
    assert idea_projects[0]["project_id"] == p2["project_id"]


def test_update_project(client, auth_headers):
    """Partially update project attributes and verify GSI status update."""
    created = client.post(
        "/api/v1/projects",
        json={"name": "Original Name", "status": "idea"},
        headers=auth_headers,
    ).json()
    project_id = created["project_id"]

    patch_resp = client.patch(
        f"/api/v1/projects/{project_id}",
        json={"name": "Updated Name", "status": "active", "live_url": "https://shipped.com"},
        headers=auth_headers,
    )
    assert patch_resp.status_code == 200
    updated = patch_resp.json()
    assert updated["name"] == "Updated Name"
    assert updated["status"] == "active"
    assert updated["live_url"] == "https://shipped.com"

    # Query GSI1 for active status to ensure GSI1SK was updated
    active_resp = client.get("/api/v1/projects?status=active", headers=auth_headers)
    assert any(p["project_id"] == project_id for p in active_resp.json())


def test_delete_project_and_cascade(client, auth_headers):
    """Delete a project and verify cascading removal of all items."""
    project = client.post(
        "/api/v1/projects",
        json={"name": "To be deleted", "status": "idea"},
        headers=auth_headers,
    ).json()
    project_id = project["project_id"]

    # Add logs
    client.post(
        f"/api/v1/projects/{project_id}/logs",
        json={"note": "Log 1", "hours_spent": 1.5},
        headers=auth_headers,
    )
    client.post(
        f"/api/v1/projects/{project_id}/logs",
        json={"note": "Log 2", "hours_spent": 2.0},
        headers=auth_headers,
    )

    # Delete project
    del_resp = client.delete(f"/api/v1/projects/{project_id}", headers=auth_headers)
    assert del_resp.status_code == 204

    # Verify project is gone
    get_proj = client.get(f"/api/v1/projects/{project_id}", headers=auth_headers)
    assert get_proj.status_code == 404

    # Verify logs are gone
    get_logs = client.get(f"/api/v1/projects/{project_id}/logs", headers=auth_headers)
    assert get_logs.status_code == 404

    # Deleting again returns 404
    del_again = client.delete(f"/api/v1/projects/{project_id}", headers=auth_headers)
    assert del_again.status_code == 404


def test_update_project_clears_optional_attribute(client, auth_headers):
    """An explicit null on an optional attribute removes it from the item."""
    created = client.post(
        "/api/v1/projects",
        json={"name": "Has a description", "description": "delete me"},
        headers=auth_headers,
    ).json()
    project_id = created["project_id"]
    assert created["description"] == "delete me"

    patch_resp = client.patch(
        f"/api/v1/projects/{project_id}",
        json={"description": None},
        headers=auth_headers,
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["description"] is None

    get_resp = client.get(f"/api/v1/projects/{project_id}", headers=auth_headers)
    assert get_resp.json()["description"] is None


def test_update_project_rejects_null_on_required_attribute(client, auth_headers):
    """Nulling name/status would produce an unreadable item, so it is a 422."""
    created = client.post(
        "/api/v1/projects",
        json={"name": "Keeps its name"},
        headers=auth_headers,
    ).json()
    project_id = created["project_id"]

    for field in ("name", "status", "tech_stack"):
        resp = client.patch(
            f"/api/v1/projects/{project_id}",
            json={field: None},
            headers=auth_headers,
        )
        assert resp.status_code == 422, field

    # The project is untouched.
    assert (
        client.get(f"/api/v1/projects/{project_id}", headers=auth_headers).json()["name"]
        == "Keeps its name"
    )
