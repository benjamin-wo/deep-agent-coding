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
import os
import shlex
import sqlite3
import time

import httpx
from e2b import Sandbox
from langchain_core.tools import tool
from langchain_deepseek import ChatDeepSeek
from langchain_e2b import E2BSandbox
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import interrupt, Command

from deepagents import create_deep_agent

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "agent_checkpoints.sqlite")

DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]
# deepseek-v4-pro: coding/agentic/long-context. deepseek-v4-flash: cheaper/faster.
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")

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
# Get IDs via Cmd/Ctrl+K in the Railway dashboard -> "Copy Project/Service/Environment ID".
RAILWAY_PROJECTS = json.loads(os.environ.get("RAILWAY_PROJECTS", "{}"))

SANDBOX_IDLE_TTL_SECONDS = int(os.environ.get("SANDBOX_IDLE_TTL_SECONDS", 20 * 60))

SYSTEM_PROMPT = """You are a coding agent reachable over Telegram, available 24/7.
You have a sandbox with shell access: clone repos, edit files, run tests,
commit changes. You may do all of this freely. The sandbox is fresh at the
start of an idle conversation, so re-clone repos if you don't see them.

The ONLY gated action is `push_to_github` -- always call it (never push via a
raw git command) when you want to publish a commit. It pauses for human
approval before anything reaches the remote. If declined, drop the change and
say so; don't retry without new instructions.

You can also check on your other Railway-deployed agent projects with
`list_railway_projects` and `check_deployment_status` -- these are read-only
and cannot start, stop, or change anything.

Keep replies concise -- this is a chat interface. Don't narrate routine tool
use, but do summarize what changed before proposing a push.
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

class _Session:
    def __init__(self):
        self.sandbox = self._make_sandbox()
        backend = E2BSandbox(sandbox=self.sandbox)
        self.agent = create_deep_agent(
            model=model,
            system_prompt=SYSTEM_PROMPT,
            checkpointer=checkpointer,
            backend=backend,
            tools=[self._make_push_tool(), list_railway_projects, check_deployment_status],
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
            result = sandbox.commands.run(cmd)
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
        session.touch()
        return session

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
    if "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        return (
            "Approve this push?\n"
            f"Repo: {payload['repo_path']}\n"
            f"Branch: {payload['branch']}\n"
            f"Message: {payload['commit_message']}\n\n"
            "Reply 'yes' to push, anything else to cancel."
        )
    last = result["messages"][-1]
    content = last.content if hasattr(last, "content") else last["content"]
    if isinstance(content, list):
        content = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    return content or "(no response)"


def run_turn(chat_id: int, text: str) -> str:
    thread_id = str(chat_id)
    config = {"configurable": {"thread_id": thread_id}}
    session = _get_session(thread_id)

    pending = get_pending_interrupt(session.agent, config)
    if pending is not None:
        approved = text.strip().lower() in ("yes", "y", "approve", "approved")
        result = session.agent.invoke(Command(resume=approved), config=config)
    else:
        result = session.agent.invoke(
            {"messages": [{"role": "user", "content": text}]},
            config=config,
        )
    return _extract_text(result)
