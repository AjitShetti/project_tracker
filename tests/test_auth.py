"""Tests for the X-API-Key check and its configuration-driven off switch.

An empty configured key disables authentication for local runs. These tests pin
both halves of that: the check still fires with a key configured (which is the
fixture default, and what test_projects.py::test_api_key_authentication asserts),
and it is genuinely off when the key is empty.

The autouse reset_settings_cache fixture in conftest.py clears the lru_cache
around get_settings before and after every test, so a test only has to clear it
once more after changing the environment mid-run.
"""

import pytest

from app.config import get_settings

PROTECTED = "/api/v1/projects"


@pytest.fixture
def open_client(monkeypatch, client):
    """A client whose configured API_KEY is empty, i.e. auth disabled."""
    monkeypatch.setenv("API_KEY", "")
    # Settings were already cached by earlier requests in this process; drop the
    # cache so the next Depends(get_settings) re-reads the patched environment.
    get_settings.cache_clear()
    assert get_settings().api_key == ""
    return client


def test_api_key_is_enforced_when_one_is_configured(client, auth_headers):
    """The fixture default sets a key, so the check must behave exactly as before."""
    assert client.get(PROTECTED).status_code == 401
    assert client.get(PROTECTED, headers={"X-API-Key": "wrong-key"}).status_code == 401
    assert client.get(PROTECTED, headers={"X-API-Key": ""}).status_code == 401
    assert client.get(PROTECTED, headers=auth_headers).status_code == 200


def test_empty_api_key_disables_the_check(open_client):
    """With no key configured, a local run needs no header on any request."""
    assert open_client.get(PROTECTED).status_code == 200

    # A junk header is not rejected either - there is nothing to compare against.
    assert open_client.get(PROTECTED, headers={"X-API-Key": "junk"}).status_code == 200


def test_empty_api_key_opens_writes_not_just_reads(open_client):
    """The off switch has to cover the whole surface, or the local loop still bites."""
    created = open_client.post(PROTECTED, json={"name": "No header needed"})
    assert created.status_code == 201
    project_id = created.json()["project_id"]

    assert (
        open_client.post(
            f"{PROTECTED}/{project_id}/logs", json={"note": "Also unauthenticated"}
        ).status_code
        == 201
    )
    assert (
        open_client.patch(f"{PROTECTED}/{project_id}", json={"status": "shipped"}).status_code
        == 200
    )
    assert open_client.delete(f"{PROTECTED}/{project_id}").status_code == 204


def test_empty_api_key_also_opens_the_deep_health_probe(open_client):
    """Deep health routes through verify_api_key, so it follows the same switch."""
    resp = open_client.get("/health?deep=true")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "table": "reachable"}


def test_shallow_health_is_unaffected_by_the_auth_setting(client):
    """Liveness was never authenticated; the switch must not change its body."""
    with_key = client.get("/health")
    assert with_key.status_code == 200
    assert with_key.json() == {"status": "ok"}


def test_shallow_health_is_unaffected_by_an_empty_key(open_client):
    """Same body with the check disabled - monitoring parses this exact object."""
    without_key = open_client.get("/health")
    assert without_key.status_code == 200
    assert without_key.json() == {"status": "ok"}
