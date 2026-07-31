import os

import httpx
import pytest

os.environ["GH_TOKEN"] = "test-token"
os.environ["GH_REPO"] = "acme/demo"

import github_tools  # noqa: E402  (env must be set before module import)
from github_tools import (  # noqa: E402
    api_comment_issue,
    api_create_issue,
    api_get_issue,
    api_list_issues,
    api_update_issue,
)


def _make_client(requests: list, real_client):
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        url = str(request.url)
        method = request.method
        if method == "POST" and url.endswith("/issues"):
            return httpx.Response(201, json={"number": 42, "title": "New ticket", "html_url": "https://github.com/acme/demo/issues/42", "state": "open", "labels": [], "assignee": None})
        if method == "GET" and "/issues/7" in url:
            return httpx.Response(200, json={"number": 7, "title": "Map", "state": "open", "labels": [{"name": "wayfinder:map"}], "assignee": None, "body": "## Destination\nthing", "html_url": "https://github.com/acme/demo/issues/7"})
        if method == "GET" and "/issues" in url:
            return httpx.Response(200, json=[
                {"number": 7, "title": "Map", "labels": [{"name": "wayfinder:map"}], "assignee": None, "body": "## Destination\nx", "html_url": "u1"},
                {"number": 8, "title": "Ticket A", "labels": [{"name": "wayfinder:grilling"}], "assignee": None, "body": "## Question\nq", "html_url": "u2"},
                {"number": 9, "title": "A PR, not an issue", "labels": [], "assignee": None, "body": "", "html_url": "u3", "pull_request": {"url": "https://api.github.com/repos/acme/demo/pulls/9"}},
            ])
        if method == "PATCH" and "/issues/8" in url:
            return httpx.Response(200, json={"number": 8, "title": "Ticket A", "state": "closed", "html_url": "https://github.com/acme/demo/issues/8"})
        if method == "POST" and "/issues/8/comments" in url:
            return httpx.Response(201, json={"html_url": "https://github.com/acme/demo/issues/8#issuecomment-1"})
        return httpx.Response(404, json={"message": "unexpected: " + str(request)})

    return real_client(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _patch_client(monkeypatch):
    requests: list = []
    real_client = httpx.Client
    monkeypatch.setattr(github_tools.httpx, "Client", lambda timeout=20: _make_client(requests, real_client))
    yield requests


def test_create_issue(_patch_client):
    issue = api_create_issue("", "New ticket", "## Question\nq?", ["wayfinder:grilling"])
    assert issue["number"] == 42
    assert issue["title"] == "New ticket"


def test_get_issue(_patch_client):
    issue = api_get_issue("", 7)
    assert issue["title"] == "Map"
    assert issue["labels"][0]["name"] == "wayfinder:map"


def test_list_issues_filters_out_pull_requests(_patch_client):
    issues = api_list_issues("", state="open", label="wayfinder")
    assert [i["number"] for i in issues] == [7, 8]  # PR #9 excluded


def test_update_issue_closes(_patch_client):
    issue = api_update_issue("", 8, state="closed")
    assert issue["state"] == "closed"


def test_update_issue_claims(_patch_client):
    issue = api_update_issue("", 8, assignees=["octocat"])
    assert issue["number"] == 8


def test_comment_issue(_patch_client):
    c = api_comment_issue("", 8, "Resolution: done")
    assert "issuecomment" in c["html_url"]


def test_missing_repo_raises(monkeypatch):
    monkeypatch.delenv("GH_REPO")
    with pytest.raises(ValueError):
        api_create_issue("", "t", "b")
