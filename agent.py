"""
Deep agent wired to OpenRouter for the model, on-demand E2B sandboxes (one
per Telegram conversation, auto-expiring when idle) for running code and
git commands, and a SQLite checkpointer so conversations AND pending
approvals survive restarts.

Two gated/read-only safety boundaries:
- push_to_github always pauses for human approval before anything reaches
  a remote repo (LangGraph interrupt).
- check_deployment_status / list_railway_projects only ever send read
  queries to Railway's GraphQL API -- structurally unable to stop, delete,
  or modify anything in your other Railway projects.
"""

import json
import logging
import os
import shlex
import sqlite3
import time

logger = logging.getLogger("deepagent-agent")

import httpx
from e2b import Sandbox
from langchain_core.tools import tool
from langchain_deepseek import ChatDeepSeek
from langchain_e2b import E2BSandbox
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import interrupt, Command

from deepagents import create_deep_agent

from interrupts import decide_resume, is_ask_interrupt, is_push_interrupt, render_ask, render_push_approval
from github_tools import api_comment_issue, api_create_issue, api_get_issue, api_list_issues, api_update_issue, _fmt_issue, _repo
from tavily_search import format_search_results, tavily_search

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "agent_checkpoints.sqlite")

DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]
# deepseek-v4-flash: fast/cheaper coding agent model. deepseek-v4-pro: long-context/heavy reasoning.
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")

model = ChatDeepSeek(model=DEEPSEEK_MODEL, api_key=DEEPSEEK_API_KEY)

GH_TOKEN = os.environ["GH_TOKEN"]  # fine-grained PAT, scoped to only the repos you want it touching
E2B_API_KEY = os.environ["E2B_API_KEY"]

# Account-scoped Railway API token (created at railway.com/account/tokens).
# Read-only usage enforced by this file, not by the token itself -- the
# token technically CAN mutate, so don't also grant raw `railway` CLI shell
# access without adding an approval gate like push_to_github has.
RAILWAY_API_TOKEN = os.environ.get("RAILWAY_API_TOKEN", "")
RAILWAY_GRAPHQL_URL = "https://backboard.railway.com/graphql/v2"

# Map friendly names -> Railway IDs for each of your other projects, e.g.:
# RAILWAY_PROJECTS='{"ben-hermes-agent": {"project_id": "...", "environment_id": "...", "service_id": "..."}}'
def _load_railway_projects() -> dict:
    val = os.environ.get("RAILWAY_PROJECTS", "").strip()
    if not val:
        return {}
    try:
        return json.loads(val)
    except Exception:
        return {}


RAILWAY_PROJECTS = _load_railway_projects()


SANDBOX_IDLE_TTL_SECONDS = int(os.environ.get("SANDBOX_IDLE_TTL_SECONDS", 20 * 60))

SYSTEM_PROMPT = """You are a coding agent reachable over Telegram, available 24/7.
You have a sandbox with shell access: clone repos, edit files, run tests,
commit changes. You may do all of this freely. The sandbox is fresh at the
start of an idle conversation, so re-clone repos if you don't see them.

The ONLY gated action is `push_to_github` -- always call it (never push via a
raw git command) when you want to publish a commit. It pauses for human
approval before anything reaches the remote. If declined, drop the change and
say so; don't retry without new instructions.

Talk WITH the user, not just at them. If a request is ambiguous, underspecified,
or hinges on a decision only the user can make, call `ask_user` with a clear
question (and options when they help) instead of guessing. Asking a short
series of questions is fine -- it's usually faster than building the wrong
thing. One caveat: never use ask_user for the push approval -- that flow is
automatic.

You can also check on your other Railway-deployed agent projects with
`list_railway_projects` and `check_deployment_status` -- these are read-only
and cannot start, stop, or change anything.

For online research (current facts, docs, pricing, news), use `search_web`
(Tavily) -- prefer it over guessing or relying on stale knowledge.

When planning complex or multi-step engineering efforts, use the wayfinder skill
located in `skills/engineering/wayfinder/SKILL.md` (or `.agents/skills/wayfinder/SKILL.md`).
Wayfinder maps and decision tickets live on GitHub Issues in the GH_REPO repo --
see `skills/engineering/wayfinder/trackers/github.md` for the exact ticket
operations (create_github_issue / list_github_issues / get_github_issue /
update_github_issue / comment_github_issue), then work the tickets sequentially
until the path is clear.

Keep replies concise -- this is a chat interface. Don't narrate routine tool
use, but do summarize what changed before proposing a push.

Format your replies to be friendly and easy to read in a Telegram chat bubble:
use concise paragraphs, clean bullet points, bold text for headings, and inline
code or code blocks. Avoid wide markdown tables or cluttered formatting.

Visual artifacts for the web app: the user may be talking to you from the web
app (voice-first). When a diagram, architecture sketch, sequence, or flowchart
would help, emit it as a ```mermaid fenced code block (flowchart, sequenceDiagram,
classDiagram, etc.) inside your reply -- the web app renders it as a real diagram
and you should say something like "I've drafted a diagram -- take a look".
For longer documents/specs, use normal markdown (headings, lists, tables); the
web app renders markdown too. Telegram just shows the code fence as code, so
this is safe in both frontends.
"""


def _make_checkpointer() -> SqliteSaver:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    return SqliteSaver(conn)


checkpointer = _make_checkpointer()


# ---------------------------------------------------------------------------
# Railway status tools (read-only, no sandbox involved)
# ---------------------------------------------------------------------------

_STATUS_QUERY = """
query($projectId: String!, $environmentId: String!, $serviceId: String!) {
  deployments(first: 3, input: {
    projectId: $projectId,
    environmentId: $environmentId,
    serviceId: $serviceId
  }) {
    edges { node { id status createdAt } }
  }
}
"""


@tool
def list_railway_projects() -> str:
    """List the project_key values you can pass to check_deployment_status."""
    if not RAILWAY_PROJECTS:
        return "No Railway projects configured (set RAILWAY_PROJECTS env var)."
    return "Known projects: " + ", ".join(RAILWAY_PROJECTS)


@tool
def check_deployment_status(project_key: str) -> str:
    """Check the 3 most recent deployment statuses of one of your other
    Railway projects. Call list_railway_projects first if unsure of the key.
    Read-only: cannot start, stop, or modify anything."""
    entry = RAILWAY_PROJECTS.get(project_key)
    if not entry:
        known = ", ".join(RAILWAY_PROJECTS) or "(none configured)"
        return f"Unknown project_key '{project_key}'. Known: {known}"

    resp = httpx.post(
        RAILWAY_GRAPHQL_URL,
        headers={"Authorization": f"Bearer {RAILWAY_API_TOKEN}"},
        json={
            "query": _STATUS_QUERY,
            "variables": {
                "projectId": entry["project_id"],
                "environmentId": entry["environment_id"],
                "serviceId": entry["service_id"],
            },
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        return f"Railway API error: {data['errors']}"

    edges = data["data"]["deployments"]["edges"]
    if not edges:
        return f"No deployments found for {project_key}."
    lines = [f"{project_key}:"]
    for edge in edges:
        node = edge["node"]
        lines.append(f"  {node['status']}  ({node['createdAt']})  id={node['id']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Per-conversation sandbox + agent, created on demand, expiring when idle
# ---------------------------------------------------------------------------

@tool
def ask_user(question: str, options: list[str] | None = None) -> str:
    """Ask the user a question in Telegram and wait for their answer. Use this
    when a request is ambiguous, needs a decision, or you need info only the
    user has. Pass short options when a quick choice is expected (max ~8).
    The user's reply (or chosen option) is returned as the tool result."""
    interrupt({"action": "ask_user", "question": question, "options": options or []})
    return "Answer received."


# --- GitHub Issues tools (wayfinder ticket integration) --------------------

@tool
def create_github_issue(title: str, body: str, labels: list[str] | None = None, repo: str = "") -> str:
    """Create a GitHub issue (wayfinder map or decision ticket) in a repo.
    repo defaults to GH_REPO env. Returns the new issue number and URL."""
    issue = api_create_issue(repo, title, body, labels)
    return f"Created issue #{issue['number']}: {issue['title']}\n{issue['html_url']}"


@tool
def list_github_issues(state: str = "open", label: str | None = None, repo: str = "") -> str:
    """List GitHub issues in a repo (pull requests excluded). Use state
    'open'/'closed'/'all' and an optional label filter like 'wayfinder'."""
    issues = api_list_issues(repo, state=state, label=label)
    if not issues:
        return f"No {state} issues{f' with label {label}' if label else ''} in {_repo(repo)}."
    return "\n".join(_fmt_issue(i) for i in issues)


@tool
def get_github_issue(number: int, repo: str = "") -> str:
    """Fetch one GitHub issue by number (full body, labels, assignee)."""
    issue = api_get_issue(repo, number)
    return (
        f"#{issue['number']} {issue['title']}\n"
        f"State: {issue['state']} | Labels: {', '.join(l['name'] for l in issue.get('labels', [])) or 'none'}\n"
        f"Assignee: {issue.get('assignee', {}).get('login') if issue.get('assignee') else 'unassigned'}\n"
        f"{issue.get('html_url')}\n\n{issue.get('body') or ''}"
    )


@tool
def update_github_issue(
    number: int,
    title: str | None = None,
    body: str | None = None,
    state: str | None = None,
    labels: list[str] | None = None,
    assignees: list[str] | None = None,
    repo: str = "",
) -> str:
    """Update a GitHub issue: edit title/body, set labels, claim it by
    assignees, or close it with state='closed'. All fields optional."""
    issue = api_update_issue(repo, number, title=title, body=body, state=state, labels=labels, assignees=assignees)
    return f"Updated issue #{issue['number']} ({issue['state']}): {issue['title']}\n{issue['html_url']}"


@tool
def comment_github_issue(number: int, body: str, repo: str = "") -> str:
    """Post a comment on a GitHub issue (e.g. a wayfinder resolution note)."""
    c = api_comment_issue(repo, number, body)
    return f"Commented on issue #{number}: {c['html_url']}"


@tool
def search_web(query: str, max_results: int = 5, search_depth: str = "basic") -> str:
    """Search the web using Tavily. Use this for online research: current
    facts, docs, pricing, news, or anything outside your local context.
    Returns a summary (when available) plus ranked results with URLs and
    snippets. Pass search_depth='advanced' for deeper analysis on complex
    queries."""
    result = tavily_search(query, max_results=max_results, search_depth=search_depth)
    return format_search_results(result, max_results=max_results)


class _Session:
    def __init__(self):
        self.sandbox = self._make_sandbox()
        backend = E2BSandbox(sandbox=self.sandbox)
        self.agent = create_deep_agent(
            model=model,
            system_prompt=SYSTEM_PROMPT,
            checkpointer=checkpointer,
            backend=backend,
            tools=[
                self._make_push_tool(),
                ask_user,
                list_railway_projects,
                check_deployment_status,
                create_github_issue,
                list_github_issues,
                get_github_issue,
                update_github_issue,
                comment_github_issue,
                search_web,
            ],
        )
        self.last_used = time.time()

    def _make_sandbox(self) -> Sandbox:
        sandbox = Sandbox.create(
            envs={"GH_TOKEN": GH_TOKEN},
            timeout=SANDBOX_IDLE_TTL_SECONDS,
        )
        sandbox.commands.run(
            "git config --global credential.helper "
            "'!f() { echo username=x-access-token; echo password=$GH_TOKEN; }; f' "
            "&& git config --global user.email 'agent@yourdomain.dev' "
            "&& git config --global user.name 'Deep Agent'"
        )
        return sandbox

    def _make_push_tool(self):
        sandbox = self.sandbox

        @tool
        def push_to_github(repo_path: str, commit_message: str, branch: str = "main") -> str:
            """Stage all changes in repo_path, commit with commit_message, and
            push to the given branch. Pauses for human approval before
            anything is pushed. Only call once the change is ready to ship."""
            approved = interrupt(
                {
                    "action": "confirm_push",
                    "repo_path": repo_path,
                    "branch": branch,
                    "commit_message": commit_message,
                }
            )
            if not approved:
                return "Push cancelled -- human did not approve."

            cmd = (
                f"cd {shlex.quote(repo_path)} && "
                f"git add -A && "
                f"git commit -m {shlex.quote(commit_message)} && "
                f"git push origin {shlex.quote(branch)}"
            )
            try:
                result = self.sandbox.commands.run(cmd)
            except Exception as e:
                return f"Push failed because the E2B sandbox timed out or was closed ({e}). Please re-apply your changes in a fresh sandbox and try again."
            if result.exit_code != 0:
                return f"Push failed (exit {result.exit_code}):\n{result.stderr}"
            return f"Pushed to {branch}.\n{result.stdout}"

        return push_to_github

    def is_expired(self) -> bool:
        return (time.time() - self.last_used) > SANDBOX_IDLE_TTL_SECONDS

    def touch(self) -> None:
        self.last_used = time.time()


_sessions: dict[str, _Session] = {}


def _get_session(thread_id: str) -> _Session:
    session = _sessions.get(thread_id)
    if session is not None and not session.is_expired():
        try:
            session.sandbox.set_timeout(SANDBOX_IDLE_TTL_SECONDS)
            session.touch()
            return session
        except Exception as e:
            logger.warning(f"Sandbox for thread_id={thread_id} timed out or dead in E2B ({e}); creating new session.")

    if session is not None:
        try:
            session.sandbox.kill()
        except Exception:
            pass

    session = _Session()
    _sessions[thread_id] = session
    return session


def get_pending_interrupt(agent, config: dict):
    state = agent.get_state(config)
    for task in state.tasks:
        if task.interrupts:
            return task.interrupts[0].value
    return None


def _extract_text(result: dict) -> str:
    messages = result.get("messages", [])
    if not messages:
        return "(no response)"
    last = messages[-1]
    content = last.content if hasattr(last, "content") else last["content"]
    if isinstance(content, list):
        content = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    if not content:
        for m in reversed(messages):
            c = m.content if hasattr(m, "content") else m["content"]
            if isinstance(c, list):
                c = "".join(
                    block.get("text", "") if isinstance(block, dict) else str(block)
                    for block in c
                )
            if c:
                return c
        return "Executed actions successfully."
    return content or "(no response)"


def _render_result(result: dict) -> dict:
    """Turn a graph result into a structured Telegram reply.

    Returns:
      {"type": "reply", "text": str}
      {"type": "ask", "text": str, "question": str, "options": [str]}
      {"type": "push_approval", "text": str}
    """
    if "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        if is_push_interrupt(payload):
            return {
                "type": "push_approval",
                "text": render_push_approval(payload),
                "payload": payload,
            }
        if is_ask_interrupt(payload):
            return {
                "type": "ask",
                "text": render_ask(payload),
                "question": str(payload.get("question") or ""),
                "options": list(payload.get("options") or []),
            }
    return {"type": "reply", "text": _extract_text(result)}


def run_turn(chat_id: int | str, text: str) -> dict:
    logger.info(f"Starting turn for chat_id={chat_id}: {text[:100]}")
    thread_id = str(chat_id)
    config = {"configurable": {"thread_id": thread_id}}
    session = _get_session(thread_id)

    pending = get_pending_interrupt(session.agent, config)
    if pending is not None:
        # A previous turn ended on an interrupt (push approval or a question).
        # Compute the resume value from the user's message and continue.
        resume_value = decide_resume(pending, text)
        result = session.agent.invoke(Command(resume=resume_value), config=config)
    else:
        result = session.agent.invoke(
            {"messages": [{"role": "user", "content": text}]},
            config=config,
        )
    reply = _render_result(result)
    logger.info(f"Completed turn for chat_id={chat_id}: type={reply['type']}, reply_len={len(reply['text'])}")
    return reply
