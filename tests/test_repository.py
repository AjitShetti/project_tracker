"""Unit tests for repository behaviour that is awkward to trigger through the API.

DynamoDB page boundaries and conditional-write failures need a table stub,
because moto will happily return everything in one page and never collide.
"""

import pytest
from botocore.exceptions import ClientError

from app import repository


def _conditional_check_failed() -> ClientError:
    return ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}},
        "PutItem",
    )


class PagedTable:
    """Table stub that returns one item per Query page."""

    def __init__(self, items):
        self._pages = items
        self.query_calls = []

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        index = 0
        start = kwargs.get("ExclusiveStartKey")
        if start is not None:
            index = start["n"]
        page = {"Items": [self._pages[index]]}
        if index + 1 < len(self._pages):
            page["LastEvaluatedKey"] = {"n": index + 1}
        return page


def test_query_all_drains_every_page():
    """A Query that spans pages must not silently truncate at 1 MB."""
    table = PagedTable([{"SK": "a"}, {"SK": "b"}, {"SK": "c"}])

    items = repository._query_all(table, KeyConditionExpression="stub")

    assert [i["SK"] for i in items] == ["a", "b", "c"]
    assert len(table.query_calls) == 3
    assert "ExclusiveStartKey" not in table.query_calls[0]
    assert table.query_calls[1]["ExclusiveStartKey"] == {"n": 1}


def test_get_project_with_logs_collects_logs_across_pages():
    """Access pattern 1 must return every log, not just the first page."""
    items = [
        {"PK": "PROJECT#p1", "SK": "METADATA", "project_id": "p1", "name": "P"},
        {"PK": "PROJECT#p1", "SK": "LOG#2026-01-01T00:00:00+00:00#a", "created_at": "2026-01-01"},
        {"PK": "PROJECT#p1", "SK": "LOG#2026-02-01T00:00:00+00:00#b", "created_at": "2026-02-01"},
    ]
    project = repository.get_project_with_logs(PagedTable(items), "p1")

    assert project is not None
    assert len(project["logs"]) == 2
    # Newest first.
    assert project["logs"][0]["created_at"] == "2026-02-01"


class DeleteTrackingTable(PagedTable):
    def __init__(self, items):
        super().__init__(items)
        self.deleted = []

    def batch_writer(self):
        table = self

        class _Batch:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def delete_item(self, Key):  # noqa: N803 - boto3's parameter name
                table.deleted.append(Key)

        return _Batch()


def test_delete_project_cascades_across_pages():
    """A cascade delete that stops at page one would orphan log items."""
    items = [
        {"PK": "PROJECT#p1", "SK": "METADATA"},
        {"PK": "PROJECT#p1", "SK": "LOG#1"},
        {"PK": "PROJECT#p1", "SK": "LOG#2"},
    ]
    table = DeleteTrackingTable(items)

    assert repository.delete_project(table, "p1") is True
    assert len(table.deleted) == 3
    assert {k["SK"] for k in table.deleted} == {"METADATA", "LOG#1", "LOG#2"}


class CollidingTable:
    """Rejects the first `failures` writes with a conditional-check failure."""

    def __init__(self, failures):
        self.failures = failures
        self.attempts = 0

    def put_item(self, **kwargs):
        self.attempts += 1
        assert "ConditionExpression" in kwargs, "create must be a conditional write"
        if self.attempts <= self.failures:
            raise _conditional_check_failed()


def test_create_project_retries_on_id_collision():
    """A truncated-UUID collision must not overwrite the existing project."""
    table = CollidingTable(failures=2)

    result = repository.create_project(table, {"name": "New", "status": "idea"})

    assert table.attempts == 3
    assert result["name"] == "New"


def test_create_project_gives_up_rather_than_overwriting():
    table = CollidingTable(failures=repository.MAX_ID_ATTEMPTS)

    with pytest.raises(RuntimeError, match="unique project_id"):
        repository.create_project(table, {"name": "New", "status": "idea"})
