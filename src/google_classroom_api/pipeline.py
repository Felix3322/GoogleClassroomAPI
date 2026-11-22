"""Lesson → Google Form → Classroom assignment → answers, over pluggable backends.

The pipeline thinks in lessons, classes, students and answers. It drives a
forms backend and a classroom backend (see :mod:`google_classroom_api.backends`)
and never talks to Google directly, so the same logic runs on the official
OAuth APIs or — for Classroom — on the reverse-engineered cookie client.

Everything Google does not keep for us lives in the
:class:`~google_classroom_api.store.Store`: which form and assignment belong
to which lesson, which Google question is which site question (kept stable
across reorders and re-edits), and a copy of every answer at the moment it was
collected.

``publish_lesson`` is idempotent: it looks at what already exists and only sends
what is missing or different, so a retry after a timeout does not create a
second form or assignment.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence, Union

from .backends import ClassroomBackend, FormSync, FormsBackend
from .errors import ClassroomAPIError, NotLinked, RecipientError
from .models import Lesson
from .store import Store
from .timeutil import to_rfc3339

ALL_STUDENTS = "all_students"

Audience = Union[str, Sequence[str]]


def judge(snapshot: Mapping[str, Any], raw: Sequence[str]) -> str:
    """``blank``, ``correct``, ``wrong`` or ``completed_ungraded`` for one raw answer.

    Judged from the site's own copy of the question, so an open question that
    was answered counts as completed even when Google assigns no score.
    """
    values = [value for value in raw if str(value).strip()]
    if not values:
        return "blank"
    correct = list(snapshot.get("correct") or [])
    if not correct:
        return "completed_ungraded"
    kind = snapshot.get("kind")
    if kind == "multiple":
        return "correct" if set(values) == set(correct) else "wrong"
    if kind == "single":
        return "correct" if values[0] in correct else "wrong"
    accepted = {str(value).strip().casefold() for value in correct}
    return "correct" if str(values[0]).strip().casefold() in accepted else "wrong"


@dataclass
class PublishResult:
    lesson_id: str
    group: str
    form_id: str
    responder_url: str
    assignment_id: str
    assignment_state: str
    assignment_link: Optional[str]
    recipients: list[str]
    questions: list[dict]
    created_form: bool
    created_assignment: bool
    form_ops_sent: int
    dropped_form_settings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class Pipeline:
    def __init__(self, forms: FormsBackend, classroom: ClassroomBackend, store: Store) -> None:
        self.forms = forms
        self.classroom = classroom
        self.store = store

    # --- students ---------------------------------------------------------------

    def sync_roster(self, course_id: int) -> list[dict]:
        """Pull the course roster into the store (OAuth backend only).

        Keyed by Google user id, so a rename or new email keeps the site
        binding. The web classroom backend has no roster read and returns
        nothing; bind students by email with :meth:`bind_student` there.
        """
        cid = str(course_id)
        rows = []
        for student in self.classroom.list_students(cid):
            self.store.upsert_student(cid, student.user_id,
                                      email=(student.email or "").strip().lower() or None,
                                      full_name=student.full_name)
            rows.append({"user_id": student.user_id, "email": student.email,
                         "full_name": student.full_name})
        return rows

    def bind_student(self, course_id: int, site_student_id: str, *,
                     email: Optional[str] = None, user_id: Optional[str] = None) -> None:
        """Tell the pipeline which site student is which Google student.

        Give ``user_id`` (the Google id, e.g. from :meth:`sync_roster`) or an
        ``email``. Responses carry the respondent's verified email, so an email
        binding is enough to map answers back; a ``user_id`` is also what lets
        the OAuth backend assign to this student by name.
        """
        cid = str(course_id)
        key = user_id
        if key is None:
            if not email:
                raise ValueError("bind_student needs either user_id or email")
            wanted = email.strip().lower()
            match = next((r for r in self.store.list_students(cid)
                          if (r["email"] or "").strip().lower() == wanted), None)
            key = match["user_id"] if match else wanted
        self.store.upsert_student(cid, key, email=(email.strip().lower() if email else None),
                                  full_name=None)
        self.store.bind_student(cid, key, site_student_id)

    def _resolve_audience(self, course_id: str, audience: Audience) -> tuple[Optional[list[str]], list[str]]:
        if audience == ALL_STUDENTS:
            return None, []
        ids = list(audience)
        if not ids:
            raise RecipientError("the recipient list is empty", empty=True)
        students = self.store.list_students(course_id)
        by_site = {r["site_student_id"]: r for r in students if r["site_student_id"]}
        by_email = {(r["email"] or "").strip().lower(): r for r in students if r["email"]}
        user_ids, site_ids, unmatched = [], [], []
        for item in ids:
            row = by_site.get(item) or by_email.get(str(item).strip().lower())
            if row is None:
                unmatched.append(item)
                continue
            user_ids.append(row["user_id"])
            site_ids.append(row["site_student_id"] or str(item))
        if unmatched:
            raise RecipientError(f"these recipients are not bound to a Google student: {unmatched}",
                                 unmatched=unmatched)
        return user_ids, site_ids

    # --- publishing -------------------------------------------------------------

    def publish_lesson(
        self,
        lesson: Lesson,
        *,
        course_id: int,
        group: str,
        audience: Audience,
        topic: Optional[str] = None,
        due: Optional[datetime] = None,
        due_has_time: bool = True,
        points: int = 100,
        draft_only: bool = False,
    ) -> PublishResult:
        """Publish one lesson to one group. Idempotent.

        ``audience`` is :data:`ALL_STUDENTS` or a list of site student ids /
        emails. Assigning to named students needs a classroom backend that
        supports it (the OAuth one); the web backend raises for a named list.
        """
        if audience != ALL_STUDENTS and not getattr(self.classroom, "supports_individual_students", False):
            raise ClassroomAPIError("this classroom backend cannot assign to named students; "
                              f"use the OAuth backend, or pass audience={ALL_STUDENTS!r}")
        store, key = self.store, (lesson.site_id, group)
        user_ids, site_ids = self._resolve_audience(str(course_id), audience)

        if store.get_link(*key) is None:
            store.create_link(*key, str(course_id), status="new")
        link = store.get_link(*key)

        # 1. The form: create once, then edit in place so question ids survive.
        known = {row["slot_key"]: row for row in store.questions(link["form_id"])} if link["form_id"] else {}
        sync: FormSync = self.forms.prepare_form(link["form_id"] or None, lesson, known)
        created_form = sync.created
        if created_form:
            store.update_link(*key, form_id=sync.form_id, status="form_created")
        self._record_questions(sync.form_id, sync, known)
        store.update_link(*key, responder_uri=sync.responder_url, status="form_ready")

        # 2. The assignment: one create, or one full update, carrying the form link.
        topic_id = self.classroom.ensure_topic(str(course_id), topic) if topic else None
        publish = not draft_only
        created_assignment = not link["coursework_id"]
        if created_assignment:
            result = self.classroom.create_assignment(
                str(course_id), title=lesson.title, instructions=lesson.description, points=points,
                topic_id=topic_id, due=due, due_has_time=due_has_time, form_url=sync.responder_url,
                student_ids=user_ids, publish=publish)
            store.update_link(*key, coursework_id=result.coursework_id, coursework_state=result.state,
                              coursework_link=result.alternate_link, attachment_id=result.attachment_id,
                              topic_id=topic_id, recipients=json.dumps(site_ids),
                              status="published" if result.state == "PUBLISHED" else "draft")
        else:
            result = self.classroom.update_assignment(
                str(course_id), link["coursework_id"], title=lesson.title,
                instructions=lesson.description, points=points, topic_id=topic_id, due=due,
                due_has_time=due_has_time, form_url=sync.responder_url, student_ids=user_ids,
                publish=publish)
            store.update_link(*key, coursework_state=result.state, coursework_link=result.alternate_link,
                              topic_id=topic_id, recipients=json.dumps(site_ids),
                              status="published" if result.state == "PUBLISHED" else "draft")

        return PublishResult(
            lesson_id=lesson.site_id, group=group, form_id=sync.form_id,
            responder_url=sync.responder_url, assignment_id=result.coursework_id,
            assignment_state=result.state, assignment_link=result.alternate_link, recipients=site_ids,
            questions=[
                {"site_question_id": slot.site_question_id, "question_id": slot.question_id,
                 "item_id": slot.item_id}
                for slot in sync.slots if slot.site_question_id is not None
            ],
            created_form=created_form, created_assignment=created_assignment,
            form_ops_sent=sync.ops_sent, dropped_form_settings=sync.dropped_settings,
        )

    def _record_questions(self, form_id: str, sync: FormSync, known: Mapping[str, Mapping]) -> None:
        now = to_rfc3339(datetime.now(timezone.utc))
        live_keys = {slot.slot_key for slot in sync.slots}
        for slot in sync.slots:
            previous = known.get(slot.slot_key)
            if not (slot.is_new or slot.changed) and previous is not None:
                continue
            version = (int(previous["version"]) + 1) if previous else 1
            self.store.save_question(
                form_id, slot.slot_key, site_question_id=slot.site_question_id,
                item_id=slot.item_id, question_id=slot.question_id, kind=slot.kind,
                version=version, content_hash=slot.content_hash)
            if slot.site_question_id is not None and slot.snapshot is not None and slot.question_id:
                self.store.add_question_version(
                    form_id, slot.question_id, version, slot.site_question_id, slot.snapshot, now)
        for dkey in sync.deleted_keys:
            if dkey not in live_keys:
                self.store.deactivate_question(form_id, dkey)

    # --- collecting answers -------------------------------------------------------

    def sync_responses(self, lesson_id: str, group: str) -> dict:
        """Fetch new and changed responses and store one record per answer.

        Returns ``created`` and ``updated`` records, plus any respondents whose
        email is not bound to a site student. Running it twice returns nothing
        the second time, so points awarded from ``created`` cannot double.
        """
        link = self._link(lesson_id, group)
        form_id, course_id = link["form_id"], link["course_id"]
        students = {(row["email"] or ""): row for row in self.store.list_students(course_id) if row["email"]}
        questions = [row for row in self.store.questions(form_id, include_inactive=True) if row["question_id"]]
        created, updated, unknown = [], [], []

        for entry in sorted(self.forms.list_responses(form_id), key=lambda e: e.submitted_ms):
            stamp = str(entry.last_submitted_ms)
            stored = self.store.get_response(form_id, entry.response_id)
            if stored and stored["last_submitted_time"] == stamp:
                continue
            email = (entry.email or "").strip().lower()
            student = students.get(email)
            if student is None or not student["site_student_id"]:
                unknown.append(entry.email or "(no email)")
                continue
            response = self.forms.get_response(form_id, entry.response_id)
            edited = stored is not None
            attempt = stored["attempt_no"] if stored else self.store.count_responses(form_id, email) + 1
            first = stored["create_time"] if stored else str(entry.submitted_ms)
            self.store.save_response(
                form_id, entry.response_id, user_id=email, email=email, create_time=first,
                last_submitted_time=stamp, attempt_no=attempt,
                total_score=(response.total or {}).get("earned_points"))
            when = to_rfc3339(datetime.fromtimestamp(entry.submitted_ms / 1000, tz=timezone.utc))
            for row in questions:
                raw = response.answers.get(row["question_id"])
                if raw is None and not row["active"]:
                    continue
                version = self.store.version_at(form_id, row["question_id"], when)
                if version is None:
                    continue
                raw = list(raw or [])
                record = {
                    "form_id": form_id, "response_id": entry.response_id,
                    "question_id": row["question_id"], "site_student_id": student["site_student_id"],
                    "user_id": email, "site_question_id": row["site_question_id"],
                    "question_version": version["version"], "attempt_no": attempt, "raw_answer": raw,
                    "status": judge(version["snapshot"], raw), "first_submitted_at": first,
                    "last_submitted_at": stamp, "only_final_version": edited,
                    "google_grade": response.scores.get(row["question_id"]),
                }
                previous = self.store.get_answer(form_id, entry.response_id, row["question_id"])
                self.store.save_answer(record)
                if previous is None:
                    created.append(record)
                elif previous["raw_answer"] != raw or previous["status"] != record["status"]:
                    updated.append(record)
        return {"created": created, "updated": updated, "unknown_respondents": unknown}

    def sync_submissions(self, lesson_id: str, group: str) -> list[dict]:
        """Store each student's Classroom submission state and grade (OAuth backend)."""
        link = self._link(lesson_id, group)
        course_id, coursework_id = link["course_id"], link["coursework_id"]
        if not coursework_id:
            raise NotLinked("the lesson has no assignment yet")
        rows = []
        for sub in self.classroom.list_submissions(course_id, coursework_id):
            self.store.save_submission(course_id, coursework_id, sub.user_id, state=sub.state,
                                       update_time=sub.update_time, history=sub.history)
            rows.append({"user_id": sub.user_id, "state": sub.state, "late": sub.late,
                         "assigned_grade": sub.assigned_grade, "draft_grade": sub.draft_grade})
        return rows

    # --- taking a lesson down -------------------------------------------------------

    def withdraw(self, lesson_id: str, group: str) -> None:
        """Close the lesson's form to new responses. Collected answers stay in the store."""
        link = self._link(lesson_id, group)
        self.forms.set_accepting_responses(link["form_id"], False)
        self.store.update_link(lesson_id, group, status="withdrawn")

    def _link(self, lesson_id: str, group: str) -> dict:
        link = self.store.get_link(lesson_id, group)
        if link is None or not link["form_id"]:
            raise NotLinked(f"lesson {lesson_id!r} has not been published for group {group!r}")
        return link


__all__ = ["ALL_STUDENTS", "Pipeline", "PublishResult", "judge", "ClassroomAPIError"]
