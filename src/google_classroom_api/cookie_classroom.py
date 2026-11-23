"""Google Classroom for a teacher, driven by the teacher's browser cookies.

This is the library's public Classroom API. It talks to the same private
endpoints as the Classroom web app (reverse-engineered, see
``docs/private-classroom-protocol.md``), so it needs no OAuth client and no
Google Cloud project — just the teacher's exported cookies. It runs headless:
no browser is involved at run time.

How far each method has been verified against the real servers is listed in
the README. Two things are still missing and raise instead of guessing:

* assigning to *named* students — the web app expresses it only when a class
  has at least two students, and the test class had one, so the request has
  not been captured;
* what a submission looks like once a student has turned work in or had it
  returned — only records carrying a teacher's draft grade have been seen.

Ids are returned as strings; every method accepts them as strings or ints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Sequence, Union

from .web.classroom import (KIND_ANNOUNCEMENT, KIND_ASSIGNMENT, KIND_MATERIAL, KIND_QUESTION,
                            STATE_PUBLISHED, ClassroomWeb, course_id_from_url)
from .web.session import WebSession

Id = Union[int, str]


class _Unchanged:
    """The default of the ``update_*`` arguments: leave that field as it is."""

    def __repr__(self) -> str:
        return "UNCHANGED"


#: Pass nothing (or this) to keep a field; pass ``None`` to clear a topic or a due date.
UNCHANGED: Any = _Unchanged()


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

    def courses(self) -> list[dict]:
        """The signed-in teacher's courses: ``[{course_id, name, section}]``."""
        return self._c.list_courses()

    def course(self, course_id: Id) -> dict:
        """``{course_id, name, section}``."""
        return self._c.course_info(course_id)

    # --- people -------------------------------------------------------------------

    def students(self, course_id: Id) -> list[dict]:
        """``[{user_id, name, email, photo_url}]`` for every student of the class."""
        return self._c.list_students(course_id)

    # --- topics -------------------------------------------------------------------

    def topics(self, course_id: Id) -> list[dict]:
        """``[{topic_id, name, updated_ms}]``."""
        return self._c.list_topics(course_id)

    def ensure_topic(self, course_id: Id, name: str) -> dict:
        """The topic with this exact name, created if it does not exist yet."""
        return self._c.ensure_topic(course_id, name)

    def rename_topic(self, course_id: Id, topic_id: str, name: str) -> None:
        self._c.rename_topic(course_id, topic_id, name)

    def delete_topic(self, course_id: Id, topic_id: str) -> None:
        """Delete a topic. The posts filed under it are kept; they lose the topic."""
        self._c.delete_topic(course_id, topic_id)

    # --- classwork ----------------------------------------------------------------

    def classwork(self, course_id: Id) -> list[dict]:
        """Everything on the Classwork page, drafts included.

        ``[{item_id, kind, title, text, state, topic_id, points, due,
        due_has_time, attachment_ids}]``. ``kind`` is ``"assignment"``,
        ``"material"`` or ``"question"``; ``state`` is 2 for published, 3 for
        draft; ``due`` is an aware UTC datetime or ``None``. A question also has
        ``choices``: the answers of a multiple-choice question, ``None`` for a
        short-answer one.
        """
        return self._c.list_classwork(course_id)

    def assignments(self, course_id: Id) -> list[dict]:
        """Assignments and assignment drafts (see :meth:`classwork` for the fields)."""
        return [row for row in self.classwork(course_id) if row["kind"] == KIND_ASSIGNMENT]

    def materials(self, course_id: Id) -> list[dict]:
        """Materials and material drafts (see :meth:`classwork` for the fields)."""
        return [row for row in self.classwork(course_id) if row["kind"] == KIND_MATERIAL]

    def questions(self, course_id: Id) -> list[dict]:
        """Questions and question drafts (see :meth:`classwork` for the fields)."""
        return [row for row in self.classwork(course_id) if row["kind"] == KIND_QUESTION]

    def item(self, course_id: Id, item_id: Id) -> dict:
        """One assignment, question, material or announcement, as :meth:`classwork` lists it.

        Raises ``LookupError`` when the class has no such item (or it was deleted).
        """
        row = self._c.get_item(course_id, item_id)
        if row is None:
            raise LookupError(f"course {course_id} has no item {item_id}")
        return row

    @staticmethod
    def is_published(item: dict) -> bool:
        return item.get("state") == STATE_PUBLISHED

    # --- changing an item: only what you pass ---------------------------------------

    def _rewrite(self, course_id: Id, item_id: Id, kind: Optional[str], *, title: Any = UNCHANGED,
                 text: Any = UNCHANGED, points: Any = UNCHANGED, topic: Any = UNCHANGED,
                 due: Any = UNCHANGED, due_has_time: bool = True, choices: Any = UNCHANGED,
                 publish: bool = False) -> dict:
        """Classroom's update call replaces the whole item, so read it first and
        send back everything that was not asked to change — attachments included."""
        now = self.item(course_id, item_id)
        if kind is not None and now["kind"] != kind:
            raise ValueError(f"item {item_id} is a {now['kind']}, not a {kind}")
        kind = now["kind"]
        title = now["title"] if title is UNCHANGED else title
        text = now["text"] if text is UNCHANGED else text
        if topic is UNCHANGED:
            topic_id = now["topic_id"]
        else:
            topic_id = self.ensure_topic(course_id, topic)["topic_id"] if topic else None
        published = publish or now["state"] == STATE_PUBLISHED
        attachments = [int(a) for a in now["attachment_ids"]]
        if kind in (KIND_MATERIAL, KIND_ANNOUNCEMENT):
            return self._c.update_item(kind, course_id, item_id, title, text=text,
                                       attachment_ids=attachments, topic_id=topic_id, publish=published)
        if due is UNCHANGED:
            due, due_has_time = now["due"], now["due_has_time"]
        common = dict(instructions=text, points=now["points"] if points is UNCHANGED else points,
                      attachment_ids=attachments, topic_id=topic_id, due=due, due_has_time=due_has_time,
                      publish=published)
        if kind == KIND_QUESTION:
            choices = (now.get("choices") or ()) if choices is UNCHANGED else (choices or ())
            return self._c.update_question(course_id, item_id, title, choices=choices, **common)
        return self._c.update_assignment(course_id, item_id, title, **common)

    def publish(self, course_id: Id, item_id: Id) -> dict:
        """Publish a draft of any kind exactly as it is. Returns ``{item_id, title, state}``."""
        return self._rewrite(course_id, item_id, None, publish=True)

    # --- assignments ----------------------------------------------------------------

    def _attachments(self, course_id: Id, item_id: int, kind: str, form_url: Optional[str],
                     link_urls: Sequence[str]) -> list[int]:
        """Attach to a draft that was just created; if that fails the draft is removed
        again, so a failed call leaves nothing behind."""
        try:
            ids = [int(self._c.attach_form(course_id, item_id, form_url))] if form_url else []
            return ids + [int(self._c.attach_link(course_id, item_id, url)) for url in link_urls]
        except Exception:
            self._c.delete_item(course_id, item_id, kind)
            raise

    def create_assignment(
        self,
        course_id: Id,
        title: str,
        *,
        instructions: str = "",
        points: int = 100,
        topic: Optional[str] = None,
        due: Optional[datetime] = None,
        due_has_time: bool = True,
        form_url: Optional[str] = None,
        link_urls: Sequence[str] = (),
        publish: bool = True,
        student_ids: Optional[Sequence[str]] = None,
    ) -> dict:
        """Create an assignment for the whole class, optionally with a Google Form
        and plain links attached.

        ``publish=False`` leaves it as a draft students cannot see. Returns
        ``{item_id, title, state}``.

        ``form_url`` must be the form's own link, ``…/forms/d/<fileId>/edit``:
        Classroom does not recognise the public responder link
        (``…/forms/d/e/…/viewform``) as a form. If attaching fails, the
        half-made draft is deleted and the error is raised.
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
        attachments = self._attachments(course_id, item_id, KIND_ASSIGNMENT, form_url, link_urls)
        if not (publish or attachments):
            return draft
        return self._c.update_assignment(course_id, item_id, title, instructions=instructions,
                                         points=points, attachment_ids=attachments, topic_id=topic_id,
                                         due=due, due_has_time=due_has_time, publish=publish)

    def update_assignment(
        self,
        course_id: Id,
        item_id: Id,
        title: Any = UNCHANGED,
        *,
        instructions: Any = UNCHANGED,
        points: Any = UNCHANGED,
        topic: Any = UNCHANGED,
        due: Any = UNCHANGED,
        due_has_time: bool = True,
        publish: bool = False,
    ) -> dict:
        """Change an assignment. Only what you pass changes; attachments are kept.

        ``topic=None`` takes it out of its topic and ``due=None`` removes the due
        date. ``publish=True`` also publishes a draft; a published assignment
        stays published either way.
        """
        return self._rewrite(course_id, item_id, KIND_ASSIGNMENT, title=title, text=instructions,
                             points=points, topic=topic, due=due, due_has_time=due_has_time,
                             publish=publish)

    def delete_assignment(self, course_id: Id, item_id: Id) -> None:
        """Delete an assignment or assignment draft."""
        self._c.delete_item(course_id, item_id, KIND_ASSIGNMENT)

    # --- questions ----------------------------------------------------------------

    def create_question(
        self,
        course_id: Id,
        question: str,
        *,
        choices: Sequence[str] = (),
        instructions: str = "",
        points: int = 100,
        topic: Optional[str] = None,
        due: Optional[datetime] = None,
        due_has_time: bool = True,
        publish: bool = True,
    ) -> dict:
        """Ask the class a question: short answer, or multiple choice when ``choices`` is given.

        ``publish=False`` leaves it as a draft students cannot see. Returns
        ``{item_id, title, state}``.
        """
        topic_id = self.ensure_topic(course_id, topic)["topic_id"] if topic else None
        draft = self._c.create_question(course_id, question, choices=choices, instructions=instructions,
                                        points=points, topic_id=topic_id, due=due,
                                        due_has_time=due_has_time)
        if not publish:
            return draft
        return self._c.update_question(course_id, int(draft["item_id"]), question, choices=choices,
                                       instructions=instructions, points=points, topic_id=topic_id,
                                       due=due, due_has_time=due_has_time, publish=True)

    def update_question(
        self,
        course_id: Id,
        item_id: Id,
        question: Any = UNCHANGED,
        *,
        choices: Any = UNCHANGED,
        instructions: Any = UNCHANGED,
        points: Any = UNCHANGED,
        topic: Any = UNCHANGED,
        due: Any = UNCHANGED,
        due_has_time: bool = True,
        publish: bool = False,
    ) -> dict:
        """Change a question. Only what you pass changes.

        ``choices=[...]`` makes it multiple choice with those answers.
        """
        return self._rewrite(course_id, item_id, KIND_QUESTION, title=question, text=instructions,
                             points=points, topic=topic, due=due, due_has_time=due_has_time,
                             choices=choices, publish=publish)

    def delete_question(self, course_id: Id, item_id: Id) -> None:
        """Delete a question or question draft of either type."""
        self._c.delete_question(course_id, item_id)

    # --- materials ------------------------------------------------------------------

    def create_material(self, course_id: Id, title: str, *, description: str = "",
                        topic: Optional[str] = None, link_urls: Sequence[str] = (),
                        publish: bool = True) -> dict:
        """Create a material (reading, links) on the Classwork page.

        ``publish=False`` keeps it a draft. Returns ``{item_id, title, state}``.
        """
        topic_id = self.ensure_topic(course_id, topic)["topic_id"] if topic else None
        draft = self._c.create_item(KIND_MATERIAL, course_id, title, text=description, topic_id=topic_id)
        item_id = int(draft["item_id"])
        attachments = self._attachments(course_id, item_id, KIND_MATERIAL, None, link_urls)
        if not (publish or attachments):
            return draft
        return self._c.update_item(KIND_MATERIAL, course_id, item_id, title, text=description,
                                   attachment_ids=attachments, topic_id=topic_id, publish=publish)

    def update_material(self, course_id: Id, item_id: Id, title: Any = UNCHANGED, *,
                        description: Any = UNCHANGED, topic: Any = UNCHANGED,
                        publish: bool = False) -> dict:
        """Change a material. Only what you pass changes; attachments are kept."""
        return self._rewrite(course_id, item_id, KIND_MATERIAL, title=title, text=description,
                             topic=topic, publish=publish)

    def delete_material(self, course_id: Id, item_id: Id) -> None:
        self._c.delete_item(course_id, item_id, KIND_MATERIAL)

    # --- announcements ------------------------------------------------------------

    def announcements(self, course_id: Id, *, drafts: bool = False) -> list[dict]:
        """Announcements on the Stream page (``text`` holds the words), or the saved drafts."""
        return self._c.list_announcements(course_id, drafts=drafts)

    def create_announcement(self, course_id: Id, text: str, *, publish: bool = True) -> dict:
        """Post an announcement to the class stream; ``publish=False`` saves a draft."""
        draft = self._c.create_item(KIND_ANNOUNCEMENT, course_id, "", text=text)
        if not publish:
            return draft
        return self._c.update_item(KIND_ANNOUNCEMENT, course_id, int(draft["item_id"]), "", text=text,
                                   publish=True)

    def update_announcement(self, course_id: Id, item_id: Id, text: Any = UNCHANGED, *,
                            publish: bool = False) -> dict:
        """Change the words of an announcement, and/or post a draft."""
        return self._rewrite(course_id, item_id, KIND_ANNOUNCEMENT, text=text, publish=publish)

    def delete_announcement(self, course_id: Id, item_id: Id) -> None:
        self._c.delete_item(course_id, item_id, KIND_ANNOUNCEMENT)

    # --- grading ------------------------------------------------------------------

    def missing_work_grade(self, course_id: Id) -> dict:
        """``{enabled, percent}``: whether missing work gets a grade automatically."""
        return self._c.get_missing_work_grade(course_id)

    def set_missing_work_grade(self, course_id: Id, *, enabled: bool,
                               percent: Optional[float] = None) -> dict:
        """Turn the default grade for missing work on or off, and set its percentage."""
        return self._c.set_missing_work_grade(course_id, enabled=enabled, percent=percent)

    def submissions(self, course_id: Id, item_id: Id) -> list[dict]:
        """Submission records of one assignment.

        ``[{student_id, item_id, updated_ms, state_code, draft_grade,
        grade_history, raw}]``. A student appears only once something happened
        to their work; students who were merely assigned it have no record.
        """
        return self._c.list_submissions(course_id, item_id)

    def submissions_raw(self, course_id: Id, item_id: Id) -> list:
        """The same records exactly as the server sent them."""
        return self._c.list_submissions_raw(course_id, item_id)

    def set_draft_grade(self, course_id: Id, item_id: Id, student_id: Id, grade: float) -> dict:
        """Give one student a draft grade (teachers only see it until work is returned)."""
        return self._c.set_draft_grade(course_id, item_id, int(student_id), grade)

    def clear_draft_grade(self, course_id: Id, item_id: Id, student_id: Id) -> dict:
        """Remove one student's draft grade."""
        return self._c.set_draft_grade(course_id, item_id, int(student_id), None)


__all__ = ["Classroom", "UNCHANGED", "course_id_from_url"]
