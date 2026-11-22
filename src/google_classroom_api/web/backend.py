"""A Classroom backend built on the reverse-engineered web RPCs (cookies).

This is the optional second classroom backend. It authenticates with exported
cookies (Classroom, unlike Docs/Forms, still accepts them off-device), and it
can do the one thing the official API cannot: set a **default grade for missing
work**. In exchange it cannot assign to named students or read the roster or
submissions — those were never reverse-engineered — so those raise.

Forms has no web backend: Google binds the Docs/Forms session to the device, so
the reverse-engineered form editor only runs inside a live browser, never from
exported cookies. Use :class:`google_classroom_api.oauth.FormsApi` for forms.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from ..backends import AssignmentResult, StudentInfo, SubmissionInfo
from ..errors import ClassroomAPIError
from .classroom import STATE_PUBLISHED, ClassroomWeb
from .session import WebSession


class WebClassroomBackend:
    """Cookie-based classroom backend. Satisfies the ClassroomBackend protocol
    except for the roster/submission/individual-student parts it cannot do."""

    supports_missing_work_grade = True
    supports_individual_students = False

    def __init__(self, session: WebSession, *, language: str = "en") -> None:
        self._c = ClassroomWeb(session, language=language)

    def list_students(self, course_id: str) -> list[StudentInfo]:
        # Course record slot 7 (student ids) + UG41I (profiles). Replayed.
        return [StudentInfo(user_id=p["user_id"], email=p["email"], full_name=p["name"])
                for p in self._c.list_students(int(course_id))]

    def ensure_topic(self, course_id: str, name: str) -> str:
        return str(self._c.ensure_topic(int(course_id), name)["topic_id"])

    def create_assignment(
        self, course_id: str, *, title: str, instructions: str = "", points: int = 100,
        topic_id: Optional[str] = None, due: Optional[datetime] = None, due_has_time: bool = True,
        form_url: Optional[str] = None, student_ids: Optional[Sequence[str]] = None,
        publish: bool = True,
    ) -> AssignmentResult:
        if student_ids:
            raise ClassroomAPIError(
                "the web classroom backend cannot assign to named students; "
                "use the OAuth classroom backend for individual students")
        cid = int(course_id)
        draft = self._c.create_assignment(cid, title, instructions=instructions, points=points,
                                          topic_id=topic_id, due=due, due_has_time=due_has_time)
        item_id = int(draft["item_id"])
        attachment_id = None
        attachment_ids: list[int] = []
        if form_url:
            attachment_id = self._c.attach_form(cid, item_id, form_url)
            attachment_ids = [int(attachment_id)]
        updated = self._c.update_assignment(
            cid, item_id, title, instructions=instructions, points=points,
            attachment_ids=attachment_ids, topic_id=topic_id, due=due,
            due_has_time=due_has_time, publish=publish)
        state = "PUBLISHED" if updated["state"] == STATE_PUBLISHED else "DRAFT"
        return AssignmentResult(coursework_id=str(item_id), state=state,
                                attachment_id=str(attachment_id) if attachment_id else None)

    def update_assignment(
        self, course_id: str, coursework_id: str, *, title: str, instructions: str = "",
        points: int = 100, topic_id: Optional[str] = None, due: Optional[datetime] = None,
        due_has_time: bool = True, form_url: Optional[str] = None,
        student_ids: Optional[Sequence[str]] = None, publish: bool = True,
    ) -> AssignmentResult:
        if student_ids:
            raise ClassroomAPIError("the web classroom backend cannot assign to named students")
        cid, item_id = int(course_id), int(coursework_id)
        attachment_id = None
        attachment_ids: list[int] = []
        if form_url:
            attachment_id = self._c.attach_form(cid, item_id, form_url)
            attachment_ids = [int(attachment_id)]
        updated = self._c.update_assignment(
            cid, item_id, title, instructions=instructions, points=points,
            attachment_ids=attachment_ids, topic_id=topic_id, due=due,
            due_has_time=due_has_time, publish=publish)
        state = "PUBLISHED" if updated["state"] == STATE_PUBLISHED else "DRAFT"
        return AssignmentResult(coursework_id=str(item_id), state=state,
                                attachment_id=str(attachment_id) if attachment_id else None)

    def list_submissions(self, course_id: str, coursework_id: str) -> list[SubmissionInfo]:
        raise ClassroomAPIError(
            "reading submissions was not reverse-engineered for the web backend; "
            "use the OAuth classroom backend to read submission state and grades")

    def set_missing_work_grade(self, course_id: str, *, enabled: bool,
                               percent: Optional[float] = None) -> dict:
        return self._c.set_missing_work_grade(int(course_id), enabled=enabled, percent=percent)


__all__ = ["WebClassroomBackend"]
