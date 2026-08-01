"""Pure helpers for worktree-diff capture (wayfinder ticket #4).

Kept dependency-free so they're unit-testable. The git command execution
itself lives in the agent's sandbox tool; this module only parses/limits
the output.
"""

MAX_DIFF_LINES = 300


def untracked_files_from_status(status_output: str) -> list[str]:
    """Extract untracked file paths from `git status --porcelain` output.

    Untracked entries start with '??'. Handles paths with spaces (the
    porcelain format quotes them; we strip the quotes).
    """
    files: list[str] = []
    for line in (status_output or "").splitlines():
        if line.startswith("??"):
            name = line[2:].strip()
            if name.startswith('"') and name.endswith('"'):
                name = name[1:-1]
            if name:
                files.append(name)
    return files


def cap_diff(diff_text: str, max_lines: int = MAX_DIFF_LINES) -> str:
    """Cap a unified diff to max_lines, appending a truncation note."""
    if not diff_text:
        return diff_text
    lines = diff_text.splitlines()
    if len(lines) <= max_lines:
        return diff_text
    kept = lines[:max_lines]
    kept.append(f"... ({len(lines) - max_lines} more lines truncated)")
    return "\n".join(kept)


def has_diff_content(diff_text: str) -> bool:
    """True if the captured diff contains actual change hunks."""
    return bool((diff_text or "").strip())
