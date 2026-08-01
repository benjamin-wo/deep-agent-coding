"""Persistent store for web-chat turns + artifacts.

Stores each user/agent turn (so the web chat history survives reloads) and
artifacts (diagrams/docs the agent produced) in a SQLite file under DATA_DIR.

Schema (deliberately simple; Postgres migration is a later swap, mirroring the
auth decision):
- turns:     id, session_id, role, text, created_at
- artifacts: id, session_id, type (diagram|doc), source (mermaid|htmlcss|markdown),
             content, saved (0/1), created_at
"""

import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, id);
CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    type TEXT NOT NULL,
    source TEXT NOT NULL,
    content TEXT NOT NULL,
    saved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifacts_session ON artifacts(session_id, id);
"""


class ArtifactStore:
    def __init__(self, data_dir: str | None = None):
        self.data_dir = data_dir or os.environ.get("DATA_DIR", "/data")
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: async callers use asyncio.to_thread, so the
        # connection is used from multiple threads; a lock serializes access.
        self._db = sqlite3.connect(str(Path(self.data_dir) / "artifacts.sqlite"), check_same_thread=False)
        self._db.executescript(_SCHEMA)
        self._db.commit()
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- turns ------------------------------------------------------------

    def add_turn(self, session_id: str, role: str, text: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO turns (session_id, role, text, created_at) VALUES (?, ?, ?, ?)",
                (session_id, role, text, _now()),
            )
            self._db.commit()

    def get_turns(self, session_id: str, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, session_id, role, text, created_at FROM turns WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [
            {"id": r[0], "session_id": r[1], "role": r[2], "text": r[3], "created_at": r[4]}
            for r in reversed(rows)  # oldest -> newest
        ]

    # -- artifacts --------------------------------------------------------

    def add_artifact(self, session_id: str, artifact_type: str, source: str, content: str, saved: bool = False) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO artifacts (session_id, type, source, content, saved, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, artifact_type, source, content, 1 if saved else 0, _now()),
            )
            self._db.commit()
            return cur.lastrowid

    def set_saved(self, artifact_id: int, saved: bool = True) -> None:
        with self._lock:
            self._db.execute("UPDATE artifacts SET saved = ? WHERE id = ?", (1 if saved else 0, artifact_id))
            self._db.commit()

    def list_artifacts(self, session_id: str | None = None, saved_only: bool = False, limit: int = 100) -> list[dict]:
        q = "SELECT id, session_id, type, source, content, saved, created_at FROM artifacts"
        conds, params = [], []
        if session_id:
            conds.append("session_id = ?")
            params.append(session_id)
        if saved_only:
            conds.append("saved = 1")
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._db.execute(q, params).fetchall()
        return [
            {"id": r[0], "session_id": r[1], "type": r[2], "source": r[3], "content": r[4], "saved": bool(r[5]), "created_at": r[6]}
            for r in rows
        ]

    def get_artifact(self, artifact_id: int) -> dict | None:
        with self._lock:
            row = self._db.execute(
                "SELECT id, session_id, type, source, content, saved, created_at FROM artifacts WHERE id = ?",
                (artifact_id,),
            ).fetchone()
        if not row:
            return None
        return {"id": row[0], "session_id": row[1], "type": row[2], "source": row[3], "content": row[4], "saved": bool(row[5]), "created_at": row[6]}


# --- artifact extraction from agent replies -------------------------------

def extract_artifacts(text: str) -> list[dict]:
    """Find [ARTIFACT:type:source] ... [/ARTIFACT] blocks in an agent reply.

    Returns [{type, source, content}]. Falls back to detecting bare mermaid
    fences when no explicit marker is present (auto-save quick sketches).
    """
    artifacts: list[dict] = []
    marker_re = re.compile(
        r"\[ARTIFACT:([a-z]+)(?::([a-z0-9_]+))?\]([\s\S]*?)\[/ARTIFACT\]",
        re.IGNORECASE,
    )
    for m in marker_re.finditer(text):
        atype = m.group(1).lower()
        source = (m.group(2) or _default_source(atype)).lower()
        content = m.group(3).strip("\n")
        artifacts.append({"type": atype, "source": source, "content": content})

    # Fallback: bare ```mermaid fences (quick sketches) -> diagram artifacts.
    mermaid_re = re.compile(r"```mermaid\s*\n?([\s\S]*?)```", re.IGNORECASE)
    for m in mermaid_re.finditer(text):
        artifacts.append({"type": "diagram", "source": "mermaid", "content": m.group(1).strip()})

    return artifacts


def _default_source(atype: str) -> str:
    return "markdown" if atype == "doc" else "mermaid"
