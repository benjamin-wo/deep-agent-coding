"""Markdown -> Telegram HTML formatting.

Pure module (no FastAPI/agent imports) so it can be unit-tested in isolation.

Rules (matching the agent's system-prompt formatting guidance):
- Code fences (```lang ... ```) and inline code become <pre>/<code> placeholders
  FIRST, so nothing inside them is interpreted as formatting.
- Text is HTML-escaped before any tag generation.
- Supported inline styles: **bold**, *italic*, _italic_, ~~strike~~, [text](url).
- Lines starting with 1-6 '#' become <b> headings.
- Lists: '- ' / '* ' / '+ ' bullets -> '• '; '- [ ]' / '- [x]' checkboxes -> '☐'/'☑'.
- Markdown tables become a monospace <pre> block (Telegram has no tables).
- Output only contains Telegram-allowed tags (<b>, <i>, <u>, <s>, <code>,
  <pre>, <a>); a final sanitizer pass drops anything else.
"""

import html
import re

_ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "a", "code", "pre", "tg-spoiler"}

_CODE_FENCE_RE = re.compile(r"```[a-zA-Z0-9_+-]*\n?(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")


def _escape(s: str) -> str:
    return html.escape(s, quote=False)


def _placeholder(kind: str, i: int) -> str:
    # Control-char delimiters: immune to the * _ ~ regexes and html.escape.
    return f"\x01{kind}{i}\x02"


def format_for_telegram(text: str) -> str:
    if not text:
        return ""

    code_blocks: list[str] = []

    def save_code_block(match: re.Match) -> str:
        # Keep leading/trailing newlines out, but preserve inner indentation.
        code = match.group(1).strip("\n")
        code_blocks.append(f"<pre>{_escape(code)}</pre>")
        return _placeholder("CB", len(code_blocks) - 1)

    inline_codes: list[str] = []

    def save_inline_code(match: re.Match) -> str:
        inline_codes.append(f"<code>{_escape(match.group(1))}</code>")
        return _placeholder("IC", len(inline_codes) - 1)

    # 1. Protect code (fences first so inline-code regex doesn't touch fence content).
    text = _CODE_FENCE_RE.sub(save_code_block, text)
    text = _INLINE_CODE_RE.sub(save_inline_code, text)

    # 2. Escape everything else.
    text = _escape(text)

    # 3. Links (before emphasis so URLs with * or _ don't get mangled).
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', text)

    # 4. Bold — allow inner italic markers and line breaks.
    text = re.sub(r"\*\*(?=\S)([\s\S]+?)(?<=\S)\*\*", r"<b>\1</b>", text)

    # 5. Italic — single * or _, boundary-aware (don't touch bullets or bold leftovers).
    text = re.sub(r"(?<!\*)\*(?!\*)(?=\S)([^*\n]+?)(?<=\S)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"(?<!_)_(?!_)(?=\S)([^_\n]+?)(?<=\S)_(?!_)", r"<i>\1</i>", text)

    # 6. Strikethrough.
    text = re.sub(r"~~([^~\n]+?)~~", r"<s>\1</s>", text)

    # 7. Headings -> bold.
    text = re.sub(r"^#{1,6}\s*(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)

    # 8. Markdown tables -> monospace block (before bullet conversion).
    text = _convert_tables(text)

    # 9. Lists & checkboxes (line-based).
    text = re.sub(r"^-\s*\[ \]\s*", "☐ ", text, flags=re.MULTILINE)
    text = re.sub(r"^-\s*\[[xX]\]\s*", "☑ ", text, flags=re.MULTILINE)
    text = re.sub(r"^[-*+]\s+", "• ", text, flags=re.MULTILINE)

    # 10. Restore protected code.
    for i, block in enumerate(code_blocks):
        text = text.replace(_placeholder("CB", i), block)
    for i, code in enumerate(inline_codes):
        text = text.replace(_placeholder("IC", i), code)

    # 11. Sanitize: drop any tag not allowed by Telegram.
    return _sanitize_tags(text)


def _convert_tables(text: str) -> str:
    """Turn markdown table blocks into readable <pre> rows."""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # A table starts with a header row containing pipes, followed by a
        # separator row of dashes/pipes.
        if (
            "|" in line
            and i + 1 < len(lines)
            and re.fullmatch(r"\s*\|?[\s:\-|]+\|?\s*", lines[i + 1])
            and re.search(r"\|", lines[i + 1])
        ):
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            rows: list[list[str]] = []
            j = i + 2
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
                j += 1
            widths = [max(len(h), *(len(r[k]) if k < len(r) else 0 for r in rows)) for k, h in enumerate(header)]
            def fmt_row(cells: list[str]) -> str:
                padded = [cells[k].ljust(widths[k]) if k < len(cells) else " " * widths[k] for k in range(len(widths))]
                return "| " + " | ".join(padded) + " |"
            table_lines = [fmt_row(header), "|" + "-+-".join("-" * w for w in widths) + "|"] + [fmt_row(r) for r in rows]
            out.append("<pre>" + _escape("\n".join(table_lines)) + "</pre>")
            i = j
        else:
            out.append(line)
            i += 1
    return "\n".join(out)


def _sanitize_tags(text: str) -> str:
    """Strip tags Telegram doesn't allow; keep their inner content."""
    def repl(match: re.Match) -> str:
        tag = (match.group(1) or "").lower()
        return match.group(0) if tag in _ALLOWED_TAGS else match.group(2)

    text = re.sub(r"</?([a-zA-Z][a-zA-Z0-9-]*)(?:\s[^>]*)?>|(<[^>]*>)", lambda m: m.group(0) if (m.group(1) or "").lower() in _ALLOWED_TAGS else (m.group(2) or ""), text)
    # Drop stray '<' that isn't part of a tag (Telegram rejects bare '<').
    text = re.sub(r"<(?![a-zA-Z/!])", "&lt;", text)
    return text
