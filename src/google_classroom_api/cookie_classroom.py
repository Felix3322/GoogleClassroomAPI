"""Google Classroom for a teacher, driven by the teacher's browser cookies.

This is the library's public Classroom API. It talks to the same private
endpoints as the Classroom web app (reverse-engineered, see
``docs/private-classroom-protocol.md``), so it needs no OAuth client and no
Google Cloud project — just the teacher's exported cookies. It runs headless:
no browser is involved at run time.

Every method below was replayed against the real Classroom servers. Two
capabilities are still missing and raise instead of guessing:

* assigning to *named* students — the web app expresses it only when a class
  has at least two students, and the test class had one, so the request has
  not been captured;
* the layout of a *populated* submission record — no student had opened or
  turned in work in the test class, so only the empty answer has been seen
  (:meth:`Classroom.submissions_raw` returns the raw records unparsed).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from .web.classroom import STATE_PUBLISHED, ClassroomWeb, course_id_from_url
from .web.session import WebSession


class Classroom:
    def __init__(self, session: WebSession, *, language: str = "en") -> None:
        self._c = ClassroomWeb(session, language=language)

    @classmethod
    def from_cookie_file(cls, path: str, **kwargs) -> "Classroom":
        """Open with a Netscape ``cookies.txt`` exported from the teacher's browser."""
        return cls(WebSession.from_cookie_file(path), **kwargs)

    # --- courses ------------------------------------------------------------------

    @staticmethod
    def course_id_from_url(url: str) -> int:
        """The course id inside any Classroom link of the course."""
        return course_id_from_url(url)

    def course(self, course_id: int) -> dict:
        """``{course_id, name, section}``."""
        return self._c.course_info(course_id)

    # --- people -------------------------------------------------------------------

    def students(self, course_id: int) -> list[dict]:
        """``[{user_id, name, email, photo_url}]`` for every student of the class."""
        return self._c.list_students(course_id)

    # --- topics -------------------------------------------------------------------

    def topics(self, course_id: int) -> list[dict]:
        return self._c.list_topics(course_id)

    def ensure_topic(self, course_id: int, name: str) -> dict:
        """The topic with this exact name, created if it does not exist yet."""
        return self._c.ensure_topic(course_id, name)

    # --- assignments --------------------------------------------------------------

    def assignments(self, course_id: int) -> list[dict]:
        """Assignments and drafts, newest first: ``[{item_id, title, state, topic_id}]``.

        ``state`` is 2 for published, 3 for draft.
        """
        return self._c.list_assignments(course_id)

    def create_assignment(
        self,
        course_id: int,
        title: str,
        *,
        instructions: str = "",
        points: int = 100,
        topic: Optional[str] = None,
        due: Optional[datetime] = None,
        due_has_time: bool = True,
        form_url: Optional[str] = None,
        publish: bool = True,
        student_ids: Optional[Sequence[str]] = None,
    ) -> dict:
        """Create an assignment for the whole class, optionally with a Google Form attached.

        ``publish=False`` leaves it as a draft students cannot see. Returns
        ``{item_id, title, state}``.
        """
        if student_ids:
            raise NotImplementedError(
                "assigning to named students is not reverse-engineered yet: the Classroom web app "
                "only expresses it in a class with at least two students, and it has not been "
                "captured. Nothing was created.")
        topic_id = self.ensure_topic(course_id, topic)["topic_id"] if topic else None
        draft = self._c.create_assignment(course_id, title, instructions=instructions, points=points,
                                          topic_id=topic_id, due=due, due_has_time=due_has_time)
        item_id = int(draft["item_id"])
        attachments = [int(self._c.attach_form(course_id, item_id, form_url))] if form_url else []
        if not (publish or attachments):
            return draft
        return self._c.update_assignment(course_id, item_id, title, instructions=instructions,
                                         points=points, attachment_ids=attachments, topic_id=topic_id,
                                         due=due, due_has_time=due_has_time, publish=publish)

    def update_assignment(
        self,
        course_id: int,
        item_id: int,
        title: str,
        *,
        instructions: str = "",
        points: int = 100,
        topic: Optional[str] = None,
        due: Optional[datetime] = None,
        due_has_time: bool = True,
        publish: bool = False,
    ) -> dict:
        """Rewrite an assignment. The whole assignment is sent, so pass every field again."""
        topic_id = self.ensure_topic(course_id, topic)["topic_id"] if topic else None
        return self._c.update_assignment(course_id, item_id, title, instructions=instructions,
                                         points=points, topic_id=topic_id, due=due,
                                         due_has_time=due_has_time, publish=publish)

    def delete_assignment(self, course_id: int, item_id: int) -> None:
        """Delete an assignment or draft."""
        self._c.delete_assignment(course_id, item_id)

    @staticmethod
    def is_published(assignment: dict) -> bool:
        return assignment.get("state") == STATE_PUBLISHED

    # --- grading settings -----------------------------------------------------------

    def missing_work_grade(self, course_id: int) -> dict:
        """``{enabled, percent}``: whether missing work gets a grade automatically."""
        return self._c.get_missing_work_grade(course_id)

    def set_missing_work_grade(self, course_id: int, *, enabled: bool,
                               percent: Optional[float] = None) -> dict:
        """Turn the default grade for missing work on or off, and set its percentage."""
        return self._c.set_missing_work_grade(course_id, enabled=enabled, percent=percent)

    # --- submissions ----------------------------------------------------------------

    def submissions_raw(self, course_id: int, item_id: int) -> list:
        """Raw student submission records of one assignment (layout not yet verified)."""
        return self._c.list_submissions_raw(course_id, item_id)


__all__ = ["Classroom", "course_id_from_url"]
