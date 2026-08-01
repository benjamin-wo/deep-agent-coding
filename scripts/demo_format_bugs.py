"""Demonstrate current formatter failures on realistic agent output."""
import sys
sys.path.insert(0, ".")
import os
os.environ["TELEGRAM_BOT_TOKEN"] = "test"
import main

samples = [
    ("italic underscore", "Tap an option, or just _reply_ with your answer."),
    ("italic star", "That's a *great* plan."),
    ("bold with inner star", "**bold *and* nested**"),
    ("markdown table", "| Name | Value |\n|------|-------|\n| Tokens | 1500 |"),
    ("code block indentation", "```py\n  indented = True\n```"),
    ("checklist", "- [ ] do this\n- [x] done"),
    ("heading + bold + link mix", "## Status\n**Repo:** [deep-agent-coding](https://github.com) - all **good**."),
    ("nested bullets", "- item\n  - sub item"),
    ("bold spanning newline", "**line one\nline two**"),
]

for name, sample in samples:
    out = main.format_for_telegram(sample)
    problems = []
    for marker in ("**", "```", "_reply_", "*great*"):
        if marker in out:
            problems.append(f"literal {marker!r}")
    print(f"--- {name} ---\n{out}\n  problems: {problems or 'none'}\n")
