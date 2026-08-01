from diff_utils import cap_diff, has_diff_content, untracked_files_from_status


def test_untracked_files_simple():
    status = " M modified.txt\n?? new_file.py\n?? another.txt\n"
    assert untracked_files_from_status(status) == ["new_file.py", "another.txt"]


def test_untracked_files_with_spaces():
    status = '?? "my file with spaces.txt"\n?? plain.txt\n'
    assert untracked_files_from_status(status) == ["my file with spaces.txt", "plain.txt"]


def test_untracked_files_empty():
    assert untracked_files_from_status("") == []
    assert untracked_files_from_status(" M tracked.txt\n") == []


def test_cap_diff_under_limit():
    diff = "\n".join(f"+line{i}" for i in range(10))
    assert cap_diff(diff, max_lines=50) == diff


def test_cap_diff_truncates():
    diff = "\n".join(f"+line{i}" for i in range(100))
    out = cap_diff(diff, max_lines=10)
    lines = out.splitlines()
    assert len(lines) == 11  # 10 kept + truncation note
    assert lines[-1].startswith("... (90 more lines truncated)")


def test_cap_diff_empty():
    assert cap_diff("") == ""


def test_has_diff_content():
    assert has_diff_content("@@ -1 +1 @@\n+hello\n") is True
    assert has_diff_content("") is False
    assert has_diff_content("   \n  ") is False
