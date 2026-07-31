"""Pure helpers for LangGraph interrupt handling (no heavy imports, unit-testable).

Two interrupt kinds exist:
- confirm_push: the push_to_github approval gate. Resume value is a boolean.
- ask_user: the interactive question tool. Resume value is the user's answer text.
"""

PUSH_ACTIONS = ("confirm_push",)
ASK_ACTIONS = ("ask_user",)


def is_push_interrupt(payload: dict) -> bool:
    return bool(payload) and payload.get("action") == "confirm_push"


def is_ask_interrupt(payload: dict) -> bool:
    return bool(payload) and payload.get("action") == "ask_user"


def decide_resume(pending: dict, user_text: str):
    """Given a pending interrupt payload and the user's message, compute the
    resume value to pass to Command(resume=...)."""
    if is_push_interrupt(pending):
        return user_text.strip().lower() in ("yes", "y", "approve", "approved")
    # ask_user (and any future free-text interrupt): resume with the raw answer.
    return user_text.strip()


def render_push_approval(payload: dict) -> str:
    return (
        "Approve this push?\n"
        f"Repo: {payload.get('repo_path')}\n"
        f"Branch: {payload.get('branch')}\n"
        f"Message: {payload.get('commit_message')}\n\n"
        "Reply 'yes' to push, anything else to cancel."
    )


def render_ask(payload: dict) -> str:
    """Render an ask_user interrupt as a friendly question, with options."""
    question = str(payload.get("question") or "").strip() or "?"
    text = f"❓ {question}"
    options = payload.get("options") or []
    if options:
        text += "\n\n" + "\n".join(f"{i + 1}. {o}" for i, o in enumerate(options))
        text += "\n\n_Tap an option, or just reply with your answer._"
    return text
