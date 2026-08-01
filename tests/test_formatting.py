import pytest

from formatting import format_for_telegram


def test_plain_text_passthrough():
    assert format_for_telegram("hello world") == "hello world"


def test_bold():
    assert format_for_telegram("this is **bold** text") == "this is <b>bold</b> text"


def test_italic_star():
    assert format_for_telegram("that's *great*") == "that's <i>great</i>"


def test_italic_underscore():
    assert format_for_telegram("just _reply_ now") == "just <i>reply</i> now"


def test_bold_with_inner_italic():
    assert format_for_telegram("**bold *and* nested**") == "<b>bold <i>and</i> nested</b>"


def test_strikethrough():
    assert format_for_telegram("~~gone~~ here") == "<s>gone</s> here"


def test_link():
    assert format_for_telegram("see [docs](https://x.dev)") == 'see <a href="https://x.dev">docs</a>'


def test_heading_becomes_bold():
    assert format_for_telegram("## Status") == "<b>Status</b>"
    assert format_for_telegram("# Big") == "<b>Big</b>"


def test_bullets():
    out = format_for_telegram("- one\n- two")
    assert out == "• one\n• two"
    out = format_for_telegram("* one\n+ two")
    assert out == "• one\n• two"


def test_checkboxes():
    out = format_for_telegram("- [ ] todo\n- [x] done")
    assert "☐ todo" in out
    assert "☑ done" in out


def test_code_block_preserved():
    out = format_for_telegram("before\n```py\nx = '<b>'\n```\nafter")
    assert "<pre>x = '&lt;b&gt;'" in out
    assert "<b>" not in out.split("<pre>")[1]  # inner content not re-interpreted


def test_code_block_keeps_indentation():
    out = format_for_telegram("```\n  indented\n```")
    assert "  indented" in out


def test_inline_code():
    assert format_for_telegram("use `git push` now") == "use <code>git push</code> now"


def test_inline_code_does_not_interpret_content():
    out = format_for_telegram("`**raw**`")
    assert "<code>**raw**</code>" in out


def test_markdown_table_becomes_pre():
    out = format_for_telegram("| Name | Value |\n|---|---|\n| Tokens | 1500 |")
    assert out.startswith("<pre>")
    assert "Tokens" in out
    assert "1500" in out
    # No raw pipes left outside the pre block
    assert out.count("|") >= 4  # pipes inside pre


def test_mermaid_fence_survives():
    out = format_for_telegram("diagram:\n```mermaid\nflowchart LR\nA-->B\n```\nend")
    assert "<pre>flowchart LR\nA--&gt;B</pre>" in out


def test_heading_with_issue_number_not_mangled():
    # "#42" (issue reference) must NOT become a heading; only "# text" does.
    assert "42" in format_for_telegram("see #42 for details")
    out = format_for_telegram("#42\n# Real heading")
    assert "<b>Real heading</b>" in out


def test_raw_angle_brackets_escaped():
    out = format_for_telegram("use <b> carefully")
    assert "&lt;b&gt;" in out or "&lt;b&gt;" in out


def test_telegram_allowed_tags_only():
    # Simulate a nasty reply; only allowed tags may survive.
    out = format_for_telegram("**b** *i* _i_ ~~s~~ [a](https://x) `c`")
    for allowed in ("<b>", "<i>", "<s>", '<a href="https://x">', "<code>"):
        assert allowed in out


def test_bold_across_lines_not_broken():
    out = format_for_telegram("**line one\nline two**")
    assert "<b>line one\nline two</b>" in out


def test_no_literal_markers_left():
    out = format_for_telegram("**bold** and *italic* and _under_ and ~~strike~~")
    assert "**" not in out
    assert "*italic*" not in out
    assert "_under_" not in out
    assert "~~" not in out
