"""SQLite store for everything Google does not keep for us.

It holds the links between the site's objects and Google's, the history of
question versions, and a snapshot of every answer at the time it was synced.
Historical answers therefore never depend on what Google still returns.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Iterable, Optional

from .timeutil import parse_ts

_SCHEMA = """
CREATE TABLE IF NOT EXISTS student (
  course_id TEXT NOT NULL, user_id TEXT NOT NULL, email TEXT, full_name TEXT,
  site_student_id TEXT, active INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (course_id, user_id));
CREATE TABLE IF NOT EXISTS link (
  lesson_id TEXT NOT NULL, grp TEXT NOT NULL, course_id TEXT NOT NULL,
  status TEXT NOT NULL, form_id TEXT, responder_uri TEXT, form_revision TEXT,
  coursework_id TEXT, coursework_state TEXT, coursework_link TEXT, topic_id TEXT,
  attachment_id TEXT, recipients TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY (lesson_id, grp));
CREATE TABLE IF NOT EXISTS question (
  form_id TEXT NOT NULL, slot_key TEXT NOT NULL, site_question_id TEXT,
  item_id TEXT, question_id TEXT, kind TEXT, version INTEGER NOT NULL DEFAULT 1,
  content_hash TEXT, active INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (form_id, slot_key));
CREATE TABLE IF NOT EXISTS question_version (
  form_id TEXT NOT NULL, question_id TEXT NOT NULL, version INTEGER NOT NULL,
  site_question_id TEXT, snapshot TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY (form_id, question_id, version));
CREATE TABLE IF NOT EXISTS response (
  form_id TEXT NOT NULL, response_id TEXT NOT NULL, user_id TEXT, email TEXT,
  create_time TEXT, last_submitted_time TEXT, attempt_no INTEGER, total_score REAL,
  PRIMARY KEY (form_id, response_id));
CREATE TABLE IF NOT EXISTS answer (
  form_id TEXT NOT NULL, response_id TEXT NOT NULL, question_id TEXT NOT NULL,
  site_student_id TEXT, user_id TEXT, site_question_id TEXT, question_version INTEGER,
  attempt_no INTEGER, raw_answer TEXT NOT NULL, status TEXT NOT NULL,
  first_submitted_at TEXT, last_submitted_at TEXT,
  only_final_version INTEGER NOT NULL DEFAULT 0, google_grade TEXT,
  PRIMARY KEY (form_id, response_id, question_id));
CREATE TABLE IF NOT EXISTS submission (
  course_id TEXT NOT NULL, coursework_id TEXT NOT NULL, user_id TEXT NOT NULL,
  state TEXT, opened_at TEXT, turned_in_at TEXT, reclaimed_at TEXT, returned_at TEXT,
  history TEXT, update_time TEXT,
  PRIMARY KEY (course_id, coursework_id, user_id));
CREATE TABLE IF NOT EXISTS resource (
  resource_id TEXT PRIMARY KEY, file_id TEXT, name TEXT, web_view_link TEXT);
CREATE TABLE IF NOT EXISTS cursor (form_id TEXT PRIMARY KEY, last_ts TEXT);
CREATE TABLE IF NOT EXISTS teacher_token (teacher_id TEXT PRIMARY KEY, tokens TEXT NOT NULL);
"""

_LINK_COLUMNS = {
    "course_id", "status", "form_id", "responder_uri", "form_revision", "coursework_id",
    "coursework_state", "coursework_link", "topic_id", "attachment_id", "recipients",
}


class Store:
    def __init__(self, path: str = "gca.sqlite3") -> None:
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.executescript(_SCHEMA)
            self._db.commit()

    def close(self) -> None:
        self._db.close()

    def _run(self, sql: str, args: Iterable[Any] = ()) -> None:
        with self._lock:
            self._db.execute(sql, tuple(args))
            self._db.commit()

    def _all(self, sql: str, args: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            return [dict(row) for row in self._db.execute(sql, tuple(args)).fetchall()]

    def _one(self, sql: str, args: Iterable[Any] = ()) -> Optional[dict]:
        rows = self._all(sql, args)
        return rows[0] if rows else None

    # --- students -------------------------------------------------------------

    def upsert_student(self, course_id: str, user_id: str, *, email: Optional[str], full_name: Optional[str]) -> None:
        """Keyed by Google user ID, so a rename or a new email keeps the site binding."""
        self._run(
            "INSERT INTO student (course_id, user_id, email, full_name, active) VALUES (?,?,?,?,1) "
            "ON CONFLICT(course_id, user_id) DO UPDATE SET "
            "email = COALESCE(excluded.email, student.email), "
            "full_name = COALESCE(excluded.full_name, student.full_name), active = 1",
            (course_id, user_id, email, full_name),
        )

    def deactivate_students_except(self, course_id: str, user_ids: Iterable[str]) -> None:
        keep = set(user_ids)
        for row in self.list_students(course_id):
            if row["user_id"] not in keep and row["active"]:
                self._run(
                    "UPDATE student SET active = 0 WHERE course_id = ? AND user_id = ?",
                    (course_id, row["user_id"]),
                )

    def bind_student(self, course_id: str, user_id: str, site_student_id: Optional[str]) -> bool:
        if not self._one("SELECT 1 AS x FROM student WHERE course_id = ? AND user_id = ?", (course_id, user_id)):
            return False
        self._run(
            "UPDATE student SET site_student_id = ? WHERE course_id = ? AND user_id = ?",
            (site_student_id, course_id, user_id),
        )
        return True

    def list_students(self, course_id: str) -> list[dict]:
        return self._all("SELECT * FROM student WHERE course_id = ? ORDER BY full_name, user_id", (course_id,))

    # --- lesson links ---------------------------------------------------------

    def get_link(self, lesson_id: str, group: str) -> Optional[dict]:
        return self._one("SELECT * FROM link WHERE lesson_id = ? AND grp = ?", (lesson_id, group))

    def create_link(self, lesson_id: str, group: str, course_id: str, status: str) -> None:
        self._run(
            "INSERT OR IGNORE INTO link (lesson_id, grp, course_id, status) VALUES (?,?,?,?)",
            (lesson_id, group, course_id, status),
        )

    def update_link(self, lesson_id: str, group: str, **fields: Any) -> None:
        unknown = set(fields) - _LINK_COLUMNS
        if unknown:
            raise KeyError(f"unknown link columns: {sorted(unknown)}")
        if not fields:
            return
        assignments = ", ".join(f"{name} = ?" for name in fields)
        self._run(
            f"UPDATE link SET {assignments} WHERE lesson_id = ? AND grp = ?",
            (*fields.values(), lesson_id, group),
        )

    def list_links(self) -> list[dict]:
        return self._all("SELECT * FROM link ORDER BY lesson_id, grp")

    # --- questions ------------------------------------------------------------

    def questions(self, form_id: str, *, include_inactive: bool = False) -> list[dict]:
        sql = "SELECT * FROM question WHERE form_id = ?"
        if not include_inactive:
            sql += " AND active = 1"
        return self._all(sql, (form_id,))

    def save_question(self, form_id: str, slot_key: str, **fields: Any) -> None:
        self._run(
            "INSERT INTO question (form_id, slot_key, site_question_id, item_id, question_id, kind, "
            "version, content_hash, active) VALUES (?,?,?,?,?,?,?,?,1) "
            "ON CONFLICT(form_id, slot_key) DO UPDATE SET site_question_id = excluded.site_question_id, "
            "item_id = excluded.item_id, question_id = excluded.question_id, kind = excluded.kind, "
            "version = excluded.version, content_hash = excluded.content_hash, active = 1",
            (
                form_id, slot_key, fields.get("site_question_id"), fields.get("item_id"),
                fields.get("question_id"), fields.get("kind"), fields.get("version", 1),
                fields.get("content_hash"),
            ),
        )

    def deactivate_question(self, form_id: str, slot_key: str) -> None:
        self._run("UPDATE question SET active = 0 WHERE form_id = ? AND slot_key = ?", (form_id, slot_key))

    def add_question_version(
        self, form_id: str, question_id: str, version: int, site_question_id: str, snapshot: dict, created_at: str
    ) -> None:
        self._run(
            "INSERT OR REPLACE INTO question_version (form_id, question_id, version, site_question_id, "
            "snapshot, created_at) VALUES (?,?,?,?,?,?)",
            (form_id, question_id, version, site_question_id, json.dumps(snapshot, ensure_ascii=False), created_at),
        )

    def question_versions(self, form_id: str, question_id: str) -> list[dict]:
        rows = self._all(
            "SELECT * FROM question_version WHERE form_id = ? AND question_id = ? ORDER BY version",
            (form_id, question_id),
        )
        for row in rows:
            row["snapshot"] = json.loads(row["snapshot"])
        return rows

    def all_question_ids(self, form_id: str) -> list[str]:
        rows = self._all(
            "SELECT DISTINCT question_id FROM question_version WHERE form_id = ?", (form_id,)
        )
        return [row["question_id"] for row in rows]

    def version_at(self, form_id: str, question_id: str, moment: str) -> Optional[dict]:
        """The version of a question that was live when an answer was submitted."""
        versions = self.question_versions(form_id, question_id)
        if not versions:
            return None
        when = parse_ts(moment)
        chosen = versions[0]
        for version in versions:
            if parse_ts(version["created_at"]) <= when:
                chosen = version
        return chosen

    # --- responses and answers ------------------------------------------------

    def get_response(self, form_id: str, response_id: str) -> Optional[dict]:
        return self._one(
            "SELECT * FROM response WHERE form_id = ? AND response_id = ?", (form_id, response_id)
        )

    def count_responses(self, form_id: str, user_id: str) -> int:
        row = self._one(
            "SELECT COUNT(*) AS n FROM response WHERE form_id = ? AND user_id = ?", (form_id, user_id)
        )
        return int(row["n"]) if row else 0

    def save_response(self, form_id: str, response_id: str, **fields: Any) -> None:
        self._run(
            "INSERT INTO response (form_id, response_id, user_id, email, create_time, last_submitted_time, "
            "attempt_no, total_score) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(form_id, response_id) DO UPDATE SET "
            "last_submitted_time = excluded.last_submitted_time, total_score = excluded.total_score",
            (
                form_id, response_id, fields.get("user_id"), fields.get("email"), fields.get("create_time"),
                fields.get("last_submitted_time"), fields.get("attempt_no"), fields.get("total_score"),
            ),
        )

    def list_responses(self, form_id: str) -> list[dict]:
        return self._all("SELECT * FROM response WHERE form_id = ? ORDER BY create_time", (form_id,))

    def get_answer(self, form_id: str, response_id: str, question_id: str) -> Optional[dict]:
        row = self._one(
            "SELECT * FROM answer WHERE form_id = ? AND response_id = ? AND question_id = ?",
            (form_id, response_id, question_id),
        )
        return _decode_answer(row) if row else None

    def save_answer(self, record: dict) -> None:
        self._run(
            "INSERT OR REPLACE INTO answer (form_id, response_id, question_id, site_student_id, user_id, "
            "site_question_id, question_version, attempt_no, raw_answer, status, first_submitted_at, "
            "last_submitted_at, only_final_version, google_grade) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                record["form_id"], record["response_id"], record["question_id"], record["site_student_id"],
                record["user_id"], record["site_question_id"], record["question_version"],
                record["attempt_no"], json.dumps(record["raw_answer"], ensure_ascii=False), record["status"],
                record["first_submitted_at"], record["last_submitted_at"],
                1 if record["only_final_version"] else 0,
                json.dumps(record.get("google_grade"), ensure_ascii=False),
            ),
        )

    def list_answers(self, form_id: str) -> list[dict]:
        rows = self._all(
            "SELECT * FROM answer WHERE form_id = ? ORDER BY first_submitted_at, response_id, question_id",
            (form_id,),
        )
        return [_decode_answer(row) for row in rows]

    def get_cursor(self, form_id: str) -> Optional[str]:
        row = self._one("SELECT last_ts FROM cursor WHERE form_id = ?", (form_id,))
        return row["last_ts"] if row else None

    def set_cursor(self, form_id: str, last_ts: str) -> None:
        self._run(
            "INSERT INTO cursor (form_id, last_ts) VALUES (?,?) "
            "ON CONFLICT(form_id) DO UPDATE SET last_ts = excluded.last_ts",
            (form_id, last_ts),
        )

    # --- classroom submissions ------------------------------------------------

    def save_submission(self, course_id: str, coursework_id: str, user_id: str, **fields: Any) -> None:
        self._run(
            "INSERT OR REPLACE INTO submission (course_id, coursework_id, user_id, state, opened_at, "
            "turned_in_at, reclaimed_at, returned_at, history, update_time) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                course_id, coursework_id, user_id, fields.get("state"), fields.get("opened_at"),
                fields.get("turned_in_at"), fields.get("reclaimed_at"), fields.get("returned_at"),
                json.dumps(fields.get("history", []), ensure_ascii=False), fields.get("update_time"),
            ),
        )

    def list_submissions(self, course_id: str, coursework_id: str) -> list[dict]:
        rows = self._all(
            "SELECT * FROM submission WHERE course_id = ? AND coursework_id = ?", (course_id, coursework_id)
        )
        for row in rows:
            row["history"] = json.loads(row["history"] or "[]")
        return rows

    # --- drive resources ------------------------------------------------------

    def get_resource(self, resource_id: str) -> Optional[dict]:
        return self._one("SELECT * FROM resource WHERE resource_id = ?", (resource_id,))

    def save_resource(self, resource_id: str, file_id: str, name: str, web_view_link: Optional[str]) -> None:
        self._run(
            "INSERT OR REPLACE INTO resource (resource_id, file_id, name, web_view_link) VALUES (?,?,?,?)",
            (resource_id, file_id, name, web_view_link),
        )

    # --- teacher tokens (used by the bundled HTTP server) ------------------------

    def get_tokens(self, teacher_id: str) -> Optional[dict]:
        row = self._one("SELECT tokens FROM teacher_token WHERE teacher_id = ?", (teacher_id,))
        return json.loads(row["tokens"]) if row else None

    def save_tokens(self, teacher_id: str, tokens: dict) -> None:
        self._run(
            "INSERT INTO teacher_token (teacher_id, tokens) VALUES (?,?) "
            "ON CONFLICT(teacher_id) DO UPDATE SET tokens = excluded.tokens",
            (teacher_id, json.dumps(tokens)),
        )


def _decode_answer(row: dict) -> dict:
    row["raw_answer"] = json.loads(row["raw_answer"])
    row["google_grade"] = json.loads(row["google_grade"]) if row.get("google_grade") else None
    row["only_final_version"] = bool(row["only_final_version"])
    return row
