import json

import pytest

from artifact_store import ArtifactStore, extract_artifacts


@pytest.fixture
def store(tmp_path):
    s = ArtifactStore(data_dir=str(tmp_path))
    yield s
    s.close()


def test_add_and_get_turns(store):
    store.add_turn("s1", "user", "hello")
    store.add_turn("s1", "agent", "hi there")
    store.add_turn("s2", "user", "other session")
    turns = store.get_turns("s1")
    assert [t["role"] for t in turns] == ["user", "agent"]
    assert turns[0]["text"] == "hello"
    assert turns[1]["text"] == "hi there"


def test_turns_oldest_first(store):
    for i in range(5):
        store.add_turn("s1", "user", f"m{i}")
    turns = store.get_turns("s1")
    assert [t["text"] for t in turns] == ["m0", "m1", "m2", "m3", "m4"]


def test_turns_isolated_by_session(store):
    store.add_turn("a", "user", "one")
    store.add_turn("b", "user", "two")
    assert len(store.get_turns("a")) == 1
    assert len(store.get_turns("b")) == 1


def test_add_artifact_and_list(store):
    aid = store.add_artifact("s1", "diagram", "mermaid", "flowchart LR\nA-->B", saved=True)
    arts = store.list_artifacts("s1")
    assert len(arts) == 1
    assert arts[0]["type"] == "diagram"
    assert arts[0]["source"] == "mermaid"
    assert arts[0]["saved"] is True
    assert store.get_artifact(aid)["content"] == "flowchart LR\nA-->B"


def test_artifacts_saved_filter(store):
    store.add_artifact("s1", "diagram", "mermaid", "x", saved=True)
    store.add_artifact("s1", "doc", "markdown", "y", saved=False)
    assert len(store.list_artifacts("s1", saved_only=False)) == 2
    assert len(store.list_artifacts("s1", saved_only=True)) == 1


def test_set_saved(store):
    aid = store.add_artifact("s1", "doc", "markdown", "y", saved=False)
    store.set_saved(aid, True)
    assert store.get_artifact(aid)["saved"] is True
    store.set_saved(aid, False)
    assert store.get_artifact(aid)["saved"] is False


def test_extract_mermaid_fence_auto_diagram():
    arts = extract_artifacts("Here's a plan:\n```mermaid\nflowchart LR\nA-->B\n```\nDone.")
    assert len(arts) == 1
    assert arts[0] == {"type": "diagram", "source": "mermaid", "content": "flowchart LR\nA-->B"}


def test_extract_explicit_htmlcss_artifact():
    text = "polished:\n[ARTIFACT:diagram:htmlcss]\n<div style='x'>hi</div>\n[/ARTIFACT]"
    arts = extract_artifacts(text)
    assert len(arts) == 1
    assert arts[0]["type"] == "diagram"
    assert arts[0]["source"] == "htmlcss"
    assert "div" in arts[0]["content"]


def test_extract_doc_artifact_default_source():
    text = "[ARTIFACT:doc]\n# Spec\nlong doc\n[/ARTIFACT]"
    arts = extract_artifacts(text)
    assert len(arts) == 1
    assert arts[0]["type"] == "doc"
    assert arts[0]["source"] == "markdown"
    assert "# Spec" in arts[0]["content"]


def test_extract_no_artifacts():
    assert extract_artifacts("just text, no diagrams") == []


def test_extract_mermaid_inside_artifact_block_does_not_duplicate():
    # An explicit [ARTIFACT:diagram:htmlcss] containing a mermaid-looking fence
    # should not ALSO trigger the bare-mermaid fallback.
    text = "[ARTIFACT:diagram:htmlcss]\n<div>not mermaid</div>\n[/ARTIFACT]"
    arts = extract_artifacts(text)
    assert len(arts) == 1
