"""GitHub Issues API helpers for the agent — wayfinder ticket integration.

Uses the GitHub REST API with the same fine-grained PAT the sandbox uses for
git pushes (GH_TOKEN). The PAT needs "Issues: Read and write" on the target
repos in addition to Contents access.

This module is deliberately free of langchain imports so unit tests can
exercise the API layer without the full dependency stack. The LangChain
tool wrappers live in agent.py.
"""

import os

import httpx

DEFAULT_REPO = os.environ.get("GH_REPO", "").strip()
_API = "https://api.github.com"


def _headers() -> dict:
    token = os.environ.get("GH_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _repo(repo: str) -> str:
    # Read env at call time so GH_REPO changes take effect without a restart.
    r = (repo or os.environ.get("GH_REPO", "")).strip().rstrip("/")
    if not r:
        raise ValueError("No repo given and GH_REPO is not set in the environment.")
    return r


def _request(method: str, url: str, **kwargs) -> httpx.Response:
    with httpx.Client(timeout=20) as client:
        resp = client.request(method, url, headers=_headers(), **kwargs)
    return resp


# --- raw REST helpers (exported for tests) ---------------------------------

def api_create_issue(repo: str, title: str, body: str, labels: list[str] | None = None) -> dict:
    resp = _request(
        "POST",
        f"{_API}/repos/{_repo(repo)}/issues",
        json={
            "title": title,
            "body": body or "",
            "labels": labels or [],
        },
    )
    resp.raise_for_status()
    return resp.json()


def api_get_issue(repo: str, number: int) -> dict:
    resp = _request("GET", f"{_API}/repos/{_repo(repo)}/issues/{number}")
    resp.raise_for_status()
    return resp.json()


def api_list_issues(repo: str, state: str = "open", label: str | None = None) -> list[dict]:
    params = {"state": state, "per_page": 100}
    if label:
        params["label"] = label
    resp = _request("GET", f"{_API}/repos/{_repo(repo)}/issues", params=params)
    resp.raise_for_status()
    # The issues endpoint also returns pull requests; wayfinder works on issues only.
    return [i for i in resp.json() if "pull_request" not in i]


def api_update_issue(
    repo: str,
    number: int,
    title: str | None = None,
    body: str | None = None,
    state: str | None = None,
    labels: list[str] | None = None,
    assignees: list[str] | None = None,
) -> dict:
    payload: dict = {}
    if title is not None:
        payload["title"] = title
    if body is not None:
        payload["body"] = body
    if state is not None:
        payload["state"] = state
    if labels is not None:
        payload["labels"] = labels
    if assignees is not None:
        payload["assignees"] = assignees
    resp = _request("PATCH", f"{_API}/repos/{_repo(repo)}/issues/{number}", json=payload)
    resp.raise_for_status()
    return resp.json()


def api_comment_issue(repo: str, number: int, body: str) -> dict:
    resp = _request(
        "POST",
        f"{_API}/repos/{_repo(repo)}/issues/{number}/comments",
        json={"body": body},
    )
    resp.raise_for_status()
    return resp.json()


def _fmt_issue(i: dict) -> str:
    labels = ", ".join(l["name"] for l in i.get("labels", [])) or "no labels"
    assignee = i.get("assignee") or (i.get("assignees") or [None])[0]
    who = assignee["login"] if isinstance(assignee, dict) else (assignee or "unassigned")
    return (
        f"#{i['number']} [{labels}] ({who}) {i['title']}\n"
        f"    {i.get('html_url')}\n"
        f"    {(i.get('body') or '').strip()[:200]}"
    )
