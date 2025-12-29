from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class RetrievalHit:
    doc_id: str
    title: str
    content: str
    tags: str
    score: float
    updated_at: Optional[float] = None


DEFAULT_PROMOTED_STATE: Dict[str, Any] = {
    "timezone": os.getenv("TIMEZONE") or os.getenv("TZ") or None,
    "location": os.getenv("LOCATION") or None,
    "preferred_units": os.getenv("PREFERRED_UNITS") or None,  # e.g. "imperial" | "metric"
    "working_rules": os.getenv("WORKING_RULES") or None,
    "tech_stack": os.getenv("TECH_STACK") or None,
    # Per-persona TTS preference: "auto" | "fish" | "elevenlabs" | "browser" | "off"
    "tts_overrides": {
        "domino": os.getenv("TTS_DOMINO") or "auto",
        "penny": os.getenv("TTS_PENNY") or "auto",
        "jimmy": os.getenv("TTS_JIMMY") or "auto",
    },
    # Convenience snapshot of key service URLs (not necessarily injected into prompts)
    "base_urls": {
        "ha": os.getenv("HA_BASE_URL") or None,
        "mistral": os.getenv("MISTRAL_BASE_URL") or None,
        "fish": os.getenv("FISH_TTS_BASE_URL") or None,
        "whisper": os.getenv("WHISPER_URL") or None,
    },

    # Fish tuning (optional overrides; runtime env still applies if unset)
    "fish_tts": {
        "timeout_sec": float(os.getenv("FISH_TTS_TIMEOUT", "120")),
        "format": os.getenv("FISH_TTS_FORMAT", "wav").lower(),
        "normalize": (os.getenv("FISH_TTS_NORMALIZE", "true").strip().lower() in ("1", "true", "yes", "on")),
        "chunk_length": 200,
        "temperature": 0.8,
        "top_p": 0.8,
        "repetition_penalty": 1.1,
        "max_new_tokens": 1024,
        "refs": {
            "domino": os.getenv("FISH_REF_DOMINO") or None,
            "penny": os.getenv("FISH_REF_PENNY") or None,
            "jimmy": os.getenv("FISH_REF_JIMMY") or None,
        },
    },

    # Whisper tuning (hub-side only)
    "whisper_stt": {
        "timeout_sec": float(os.getenv("WHISPER_TIMEOUT", "60")),
    },
    # Retrieval memory off by default to avoid “creepy/wrong” behavior
    "retrieval_enabled": False,
}


class MemoryStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # -----------------
    # DB primitives
    # -----------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row

        # Concurrency hardening:
        # - WAL reduces writer lock contention
        # - busy_timeout reduces intermittent failures under concurrent writes
        # Keep transactions short by using context managers per operation.
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
        except Exception:
            pass
        try:
            conn.execute("PRAGMA synchronous=NORMAL;")
        except Exception:
            pass
        try:
            conn.execute("PRAGMA busy_timeout=2000;")
        except Exception:
            pass
        try:
            conn.execute("PRAGMA foreign_keys=ON;")
        except Exception:
            pass

        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    last_seen REAL NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS promoted_state (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    persona TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    ts REAL NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_summaries (
                    session_id TEXT NOT NULL,
                    persona TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (session_id, persona)
                )
                """
            )

            # Retrieval metadata (for corpus size caps + pruning)
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS retrieval_meta (
                    doc_id TEXT PRIMARY KEY,
                    updated_at REAL NOT NULL,
                    size_chars INTEGER NOT NULL
                )
                """
            )
            # FTS5 retrieval store
            try:
                cur.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS retrieval_fts
                    USING fts5(doc_id UNINDEXED, title, content, tags)
                    """
                )
            except sqlite3.OperationalError:
                # If FTS5 is unavailable, we still keep the app working.
                pass

            conn.commit()

        # Seed defaults if empty
        existing = self.get_promoted_state()
        if not existing:
            self.set_promoted_state(DEFAULT_PROMOTED_STATE)

    # -----------------
    # Sessions
    # -----------------

    def touch_session(self, session_id: str, max_age_days: int = 30) -> None:
        """Record last_seen for a pseudonymous session_id.

        Note: session_id is NOT authentication; it is only for grouping history.
        """
        if not session_id:
            return
        now = time.time()
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT session_id FROM sessions WHERE session_id=?", (session_id,))
            row = cur.fetchone()
            if row:
                cur.execute("UPDATE sessions SET last_seen=? WHERE session_id=?", (now, session_id))
            else:
                cur.execute(
                    "INSERT INTO sessions(session_id, created_at, last_seen) VALUES (?, ?, ?)",
                    (session_id, now, now),
                )
            conn.commit()

        # Opportunistic cleanup (keep it cheap)
        try:
            self.expire_stale_sessions(max_age_days=max_age_days)
        except Exception:
            pass

    def expire_stale_sessions(self, max_age_days: int = 30) -> int:
        cutoff = time.time() - (float(max_age_days) * 86400.0)
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT session_id FROM sessions WHERE last_seen < ?", (cutoff,))
            stale = [r["session_id"] for r in cur.fetchall()]
            if not stale:
                return 0

            for sid in stale:
                cur.execute("DELETE FROM chat_messages WHERE session_id=?", (sid,))
                cur.execute("DELETE FROM chat_summaries WHERE session_id=?", (sid,))
                cur.execute("DELETE FROM sessions WHERE session_id=?", (sid,))
            conn.commit()
        return len(stale)

    # -----------------
    # Promoted state
    # -----------------

    def get_promoted_state(self) -> Dict[str, Any]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT key, value_json FROM promoted_state")
            rows = cur.fetchall()
        if not rows:
            return {}
        out: Dict[str, Any] = {}
        for r in rows:
            try:
                out[r["key"]] = json.loads(r["value_json"])
            except Exception:
                out[r["key"]] = r["value_json"]
        return out

    def set_promoted_state(self, state: Dict[str, Any]) -> None:
        now = time.time()
        with self._connect() as conn:
            cur = conn.cursor()
            for k, v in (state or {}).items():
                cur.execute(
                    "INSERT OR REPLACE INTO promoted_state(key, value_json, updated_at) VALUES (?, ?, ?)",
                    (k, json.dumps(v, ensure_ascii=False), now),
                )
            conn.commit()

    def patch_promoted_state(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        state = self.get_promoted_state()

        # Shallow merge for most keys; deep merge for known nested dicts.
        for k, v in (patch or {}).items():
            if k in ("tts_overrides", "base_urls", "fish_tts", "whisper_stt") and isinstance(state.get(k), dict) and isinstance(v, dict):
                merged = dict(state.get(k) or {})
                merged.update(v)
                state[k] = merged
            else:
                state[k] = v

        self.set_promoted_state(state)
        return state

    # -----------------
    # Rolling chat history
    # -----------------

    def add_chat_message(self, session_id: str, persona: str, role: str, content: str) -> None:
        if not content:
            return
        now = time.time()
        with self._connect() as conn:
            cur = conn.cursor()

            # De-dupe common retry patterns.
            # If an upstream LLM call fails/hangs after we record the user turn,
            # the next retry can otherwise store identical "User: ..." lines twice.
            try:
                cur.execute(
                    """
                    SELECT content, ts
                    FROM chat_messages
                    WHERE session_id=? AND persona=? AND role=?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (session_id, persona, role),
                )
                row = cur.fetchone()
                if row:
                    last_content = (row["content"] or "").strip()
                    last_ts = float(row["ts"] or 0.0)
                    if last_content == content.strip() and (now - last_ts) < 300.0:
                        return
            except Exception:
                pass

            cur.execute(
                "INSERT INTO chat_messages(session_id, persona, role, content, ts) VALUES (?, ?, ?, ?, ?)",
                (session_id, persona, role, content, now),
            )
            conn.commit()

    def get_chat_context(
        self,
        session_id: str,
        persona: str,
        last_n: int,
        max_chars: int,
    ) -> Tuple[str, List[Dict[str, str]]]:
        summary = ""
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT summary FROM chat_summaries WHERE session_id=? AND persona=?",
                (session_id, persona),
            )
            row = cur.fetchone()
            if row:
                summary = row["summary"] or ""

            cur.execute(
                """
                SELECT role, content
                FROM chat_messages
                WHERE session_id=? AND persona=?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, persona, max(1, int(last_n))),
            )
            rows = cur.fetchall()

        messages = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

        # Aggressive trim by chars
        def total_chars() -> int:
            return sum(len(m["content"]) for m in messages) + len(summary)

        while messages and total_chars() > max_chars:
            # drop oldest message first
            messages.pop(0)

        return summary, messages

    def trim_history(self, session_id: str, persona: str, keep_last: int, max_summary_chars: int = 1800) -> None:
        """Trim stored history by moving old messages into a crude summary.

        This is intentionally simple (no LLM summarization) to keep it predictable.
        """
        keep_last = max(4, int(keep_last))

        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, role, content FROM chat_messages WHERE session_id=? AND persona=? ORDER BY id ASC",
                (session_id, persona),
            )
            rows = cur.fetchall()
            if len(rows) <= keep_last:
                return

            to_summarize = rows[: max(0, len(rows) - keep_last)]
            to_keep_from_id = rows[len(rows) - keep_last]["id"]

            cur.execute(
                "SELECT summary FROM chat_summaries WHERE session_id=? AND persona=?",
                (session_id, persona),
            )
            existing = cur.fetchone()
            summary = (existing["summary"] if existing else "") or ""

            # Compact digest (single stable field, continually compacted)
            digest_parts: List[str] = []
            for r in to_summarize:
                role = r["role"]
                content = (r["content"] or "").strip().replace("\n", " ")
                content = content[:240]
                digest_parts.append(f"{role}: {content}")
            digest = " | ".join([p for p in digest_parts if p])
            combined = (summary + " | " + digest) if summary and digest else (summary or digest)
            combined = (combined or "").replace("\n", " ").strip()
            if len(combined) > max_summary_chars:
                combined = combined[-max_summary_chars:]
            summary = combined
            now = time.time()
            cur.execute(
                "INSERT OR REPLACE INTO chat_summaries(session_id, persona, summary, updated_at) VALUES (?, ?, ?, ?)",
                (session_id, persona, summary, now),
            )

            cur.execute(
                "DELETE FROM chat_messages WHERE session_id=? AND persona=? AND id < ?",
                (session_id, persona, to_keep_from_id),
            )
            conn.commit()

    def clear_history(self, session_id: str) -> None:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM chat_messages WHERE session_id=?", (session_id,))
            cur.execute("DELETE FROM chat_summaries WHERE session_id=?", (session_id,))
            conn.commit()

    # -----------------
    # Retrieval memory
    # -----------------

    def retrieval_available(self) -> bool:
        try:
            with self._connect() as conn:
                conn.execute("SELECT 1 FROM retrieval_fts LIMIT 1")
            return True
        except Exception:
            return False

    def upsert_retrieval_doc(self, doc_id: str, title: str, content: str, tags: Optional[str] = None) -> None:
        if not doc_id:
            raise ValueError("doc_id is required")
        if not self.retrieval_available():
            raise RuntimeError("FTS5 is not available in this SQLite build")

        tags = tags or ""
        with self._connect() as conn:
            cur = conn.cursor()
            # Delete + insert is simplest for FTS tables
            cur.execute("DELETE FROM retrieval_fts WHERE doc_id=?", (doc_id,))
            cur.execute(
                "INSERT INTO retrieval_fts(doc_id, title, content, tags) VALUES (?, ?, ?, ?)",
                (doc_id, title or "", content or "", tags),
            )

            now = time.time()
            size_chars = int(len(title or "") + len(content or "") + len(tags or ""))
            cur.execute(
                "INSERT OR REPLACE INTO retrieval_meta(doc_id, updated_at, size_chars) VALUES (?, ?, ?)",
                (doc_id, now, size_chars),
            )
            conn.commit()

    def delete_retrieval_doc(self, doc_id: str) -> None:
        if not doc_id or not self.retrieval_available():
            return
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM retrieval_fts WHERE doc_id=?", (doc_id,))
            cur.execute("DELETE FROM retrieval_meta WHERE doc_id=?", (doc_id,))
            conn.commit()

    def purge_retrieval(self) -> None:
        if not self.retrieval_available():
            return
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM retrieval_fts")
            cur.execute("DELETE FROM retrieval_meta")
            conn.commit()

    def prune_retrieval_to_max_chars(self, max_total_chars: int) -> int:
        """Prune oldest retrieval docs until estimated corpus size is under max_total_chars."""
        if not self.retrieval_available():
            return 0
        max_total_chars = int(max_total_chars)
        if max_total_chars <= 0:
            return 0

        removed = 0
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COALESCE(SUM(size_chars), 0) AS total FROM retrieval_meta")
            total = int(cur.fetchone()["total"])
            if total <= max_total_chars:
                return 0

            cur.execute("SELECT doc_id, size_chars FROM retrieval_meta ORDER BY updated_at ASC")
            rows = cur.fetchall()
            for r in rows:
                if total <= max_total_chars:
                    break
                doc_id = r["doc_id"]
                sz = int(r["size_chars"])
                cur.execute("DELETE FROM retrieval_fts WHERE doc_id=?", (doc_id,))
                cur.execute("DELETE FROM retrieval_meta WHERE doc_id=?", (doc_id,))
                total -= sz
                removed += 1

            conn.commit()
        return removed

    def query_retrieval(self, query: str, limit: int = 3) -> List[RetrievalHit]:
        if not query:
            return []
        if not self.retrieval_available():
            return []

        q = query.strip()
        limit = max(1, min(int(limit), 10))

        with self._connect() as conn:
            cur = conn.cursor()
            # bm25() is available with FTS5; lower is better.
            # Join meta to expose updated_at for debugging + prompt metadata.
            cur.execute(
                """
                SELECT f.doc_id, f.title, f.content, f.tags,
                       bm25(retrieval_fts) AS score,
                       m.updated_at AS updated_at
                FROM retrieval_fts AS f
                LEFT JOIN retrieval_meta AS m ON m.doc_id = f.doc_id
                WHERE retrieval_fts MATCH ?
                ORDER BY score
                LIMIT ?
                """,
                (q, limit),
            )
            rows = cur.fetchall()

        hits: List[RetrievalHit] = []
        for r in rows:
            hits.append(
                RetrievalHit(
                    doc_id=r["doc_id"],
                    title=r["title"],
                    content=r["content"],
                    tags=r["tags"],
                    score=float(r["score"]),
                    updated_at=(float(r["updated_at"]) if r["updated_at"] is not None else None),
                )
            )
        return hits

    def sync_from_markdown(self, file_path: Path) -> int:
        """Syncs content from a markdown file into the retrieval store.
        
        - Parses headers (#) as separate documents.
        - Replaces all existing docs with tag='source:manual'.
        """
        if not self.retrieval_available():
            return 0
        
        p = Path(file_path)
        if not p.exists():
            return 0
            
        text = p.read_text(encoding="utf-8")
        
        # 1. Parse sections
        sections: List[Tuple[str, str]] = []
        current_title = "General"
        current_lines = []
        
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("#"):
                # New section
                if current_lines:
                    sections.append((current_title, "\n".join(current_lines)))
                current_title = line.lstrip("#").strip()
                current_lines = []
            elif line:
                current_lines.append(line)
                
        if current_lines:
            sections.append((current_title, "\n".join(current_lines)))
            
        # 2. Clear old manual entries
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM retrieval_fts WHERE tags LIKE '%source:manual%'")
            # Also clean up meta (orphaned meta is fine but let's be clean if we can, 
            # though FTS delete doesn't cascade to meta automatically in this schema.
            # We'll just leave orphaned meta for now or do a subquery delete if needed,
            # but for simplicity we just insert new ones.)
            conn.commit()
            
        # 3. Insert new
        count = 0
        for title, content in sections:
            if not content.strip():
                continue
            # Deterministic ID based on title to avoid churn if possible, 
            # but simple uuid is safer for now.
            import uuid
            doc_id = f"manual_{uuid.uuid4().hex[:8]}"
            self.upsert_retrieval_doc(doc_id, title, content, tags="source:manual")
            count += 1
            
        return count

