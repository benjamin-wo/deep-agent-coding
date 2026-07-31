from interrupts import (
    decide_resume,
    is_ask_interrupt,
    is_push_interrupt,
    render_ask,
    render_push_approval,
)


def test_push_interrupt_detection():
    assert is_push_interrupt({"action": "confirm_push", "repo_path": "/x"})
    assert not is_push_interrupt({"action": "ask_user", "question": "hi"})
    assert not is_push_interrupt(None)


def test_ask_interrupt_detection():
    assert is_ask_interrupt({"action": "ask_user", "question": "hi"})
    assert not is_ask_interrupt({"action": "confirm_push"})


def test_decide_resume_push_yes_variants():
    payload = {"action": "confirm_push"}
    for yes in ("yes", "Yes", "y", "approve", "APPROVED"):
        assert decide_resume(payload, yes) is True
    for no in ("no", "nope", "cancel", ""):
        assert decide_resume(payload, no) is False


def test_decide_resume_ask_returns_raw_text():
    payload = {"action": "ask_user", "question": "Which stack?"}
    assert decide_resume(payload, "Python") == "Python"
    assert decide_resume(payload, "  fastapi  ") == "fastapi"
    # "yes" answers the question; it must NOT be treated as push approval
    assert decide_resume(payload, "yes") == "yes"


def test_render_push_approval():
    text = render_push_approval(
        {"action": "confirm_push", "repo_path": "/home/user/repo", "branch": "main", "commit_message": "fix bug"}
    )
    assert "Approve this push?" in text
    assert "/home/user/repo" in text
    assert "fix bug" in text
    assert "Reply 'yes' to push" in text


def test_render_ask_without_options():
    text = render_ask({"action": "ask_user", "question": "Which stack?"})
    assert "Which stack?" in text
    assert "1." not in text


def test_render_ask_with_options():
    text = render_ask({"action": "ask_user", "question": "How should I proceed?", "options": ["Plan only", "Just do it"]})
    assert "How should I proceed?" in text
    assert "1. Plan only" in text
    assert "2. Just do it" in text
