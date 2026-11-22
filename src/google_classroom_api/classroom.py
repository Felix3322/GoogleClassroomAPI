"""Google Classroom API v1 (official)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional, Sequence

from .errors import GoogleApiError, RecipientError
from .transport import GoogleSession

BASE = "https://classroom.googleapis.com/v1"


def due_fields(due: datetime) -> dict:
    """Classroom stores the due date and time separately, both in UTC."""
    if due.tzinfo is None:
        raise ValueError("due must be a timezone-aware datetime")
    utc = due.astimezone(timezone.utc)
    return {
        "dueDate": {"year": utc.year, "month": utc.month, "day": utc.day},
        "dueTime": {"hours": utc.hour, "minutes": utc.minute},
    }


def same_due(coursework: dict, due: datetime) -> bool:
    wanted = due_fields(due)
    date = coursework.get("dueDate") or {}
    clock = coursework.get("dueTime") or {}
    return (
        (date.get("year"), date.get("month"), date.get("day"))
        == (wanted["dueDate"]["year"], wanted["dueDate"]["month"], wanted["dueDate"]["day"])
        and (clock.get("hours", 0), clock.get("minutes", 0))
        == (wanted["dueTime"]["hours"], wanted["dueTime"]["minutes"])
    )


def coursework_body(
    *,
    title: str,
    student_ids: Sequence[str],
    description: str = "",
    link_urls: Iterable[str] = (),
    topic_id: Optional[str] = None,
    due: Optional[datetime] = None,
    max_points: Optional[int] = None,
) -> dict:
    """Body for ``courseWork.create``: always a draft, always for named students.

    Google's default when ``assigneeMode`` is missing is ``ALL_STUDENTS``, so this
    helper never leaves it out and refuses an empty list.
    """
    ids = list(dict.fromkeys(student_ids))
    if not ids:
        raise RecipientError("recipient list is empty; refusing to build coursework", empty=True)
    body: dict = {
        "title": title,
        "workType": "ASSIGNMENT",
        "state": "DRAFT",
        "assigneeMode": "INDIVIDUAL_STUDENTS",
        "individualStudentsOptions": {"studentIds": ids},
    }
    if description:
        body["description"] = description
    materials = [{"link": {"url": url}} for url in link_urls]
    if materials:
        body["materials"] = materials
    if topic_id:
        body["topicId"] = topic_id
    if due is not None:
        body.update(due_fields(due))
    if max_points is not None:
        body["maxPoints"] = max_points
    return body


class ClassroomApi:
    def __init__(self, session: GoogleSession) -> None:
        self._s = session

    # --- courses and roster -------------------------------------------------

    def list_courses(self, *, teacher_id: str = "me", states: Sequence[str] = ("ACTIVE",)) -> list[dict]:
        params = {"teacherId": teacher_id, "courseStates": list(states), "pageSize": 100}
        return list(self._s.paginate(f"{BASE}/courses", "courses", params=params))

    def list_students(self, course_id: str) -> list[dict]:
        url = f"{BASE}/courses/{course_id}/students"
        return list(self._s.paginate(url, "students", params={"pageSize": 100}))

    # --- topics -------------------------------------------------------------

    def list_topics(self, course_id: str) -> list[dict]:
        return list(self._s.paginate(f"{BASE}/courses/{course_id}/topics", "topic"))

    def create_topic(self, course_id: str, name: str) -> dict:
        return self._s.request("POST", f"{BASE}/courses/{course_id}/topics", json={"name": name})

    def ensure_topic(self, course_id: str, name: str) -> dict:
        """Reuse the topic with this exact name, or create it."""
        wanted = " ".join(name.split())
        for topic in self.list_topics(course_id):
            if topic.get("name") == wanted:
                return topic
        try:
            return self.create_topic(course_id, wanted)
        except GoogleApiError as exc:
            if exc.status != 409 and exc.reason != "ALREADY_EXISTS":
                raise
        for topic in self.list_topics(course_id):
            if topic.get("name") == wanted:
                return topic
        raise GoogleApiError(409, f"topic {wanted!r} reported as existing but not listed")

    # --- coursework ---------------------------------------------------------

    def create_coursework(self, course_id: str, body: dict) -> dict:
        # Named students are the primary mode; refuse an individual-students
        # body with an empty roster so it does not silently fall back to the
        # whole class. ALL_STUDENTS is allowed for deliberate whole-class work.
        if body.get("assigneeMode") == "INDIVIDUAL_STUDENTS" and not body.get(
                "individualStudentsOptions", {}).get("studentIds"):
            raise RecipientError("recipient list is empty; refusing to create coursework", empty=True)
        return self._s.request("POST", f"{BASE}/courses/{course_id}/courseWork", json=body)

    def get_coursework(self, course_id: str, coursework_id: str) -> dict:
        return self._s.request("GET", f"{BASE}/courses/{course_id}/courseWork/{coursework_id}")

    def list_coursework(
        self, course_id: str, *, states: Sequence[str] = ("DRAFT", "PUBLISHED")
    ) -> list[dict]:
        params = {"courseWorkStates": list(states), "pageSize": 100}
        return list(
            self._s.paginate(f"{BASE}/courses/{course_id}/courseWork", "courseWork", params=params)
        )

    def patch_coursework(self, course_id: str, coursework_id: str, fields: dict) -> dict:
        """Only the project that created the coursework may patch it."""
        return self._s.request(
            "PATCH",
            f"{BASE}/courses/{course_id}/courseWork/{coursework_id}",
            params={"updateMask": ",".join(fields.keys())},
            json=fields,
        )

    def delete_coursework(self, course_id: str, coursework_id: str) -> None:
        self._s.request("DELETE", f"{BASE}/courses/{course_id}/courseWork/{coursework_id}")

    def modify_assignees(
        self,
        course_id: str,
        coursework_id: str,
        *,
        add: Iterable[str] = (),
        remove: Iterable[str] = (),
    ) -> dict:
        body = {
            "assigneeMode": "INDIVIDUAL_STUDENTS",
            "modifyIndividualStudentsOptions": {
                "addStudentIds": list(add),
                "removeStudentIds": list(remove),
            },
        }
        return self._s.request(
            "POST",
            f"{BASE}/courses/{course_id}/courseWork/{coursework_id}:modifyAssignees",
            json=body,
            idempotent=True,
        )

    def list_submissions(self, course_id: str, coursework_id: str) -> list[dict]:
        url = f"{BASE}/courses/{course_id}/courseWork/{coursework_id}/studentSubmissions"
        return list(self._s.paginate(url, "studentSubmissions", params={"pageSize": 100}))

    # --- student groups (optional mirror of the site's grades) ---------------

    def list_student_groups(self, course_id: str) -> list[dict]:
        return list(self._s.paginate(f"{BASE}/courses/{course_id}/studentGroups", "studentGroups"))

    def create_student_group(self, course_id: str, title: str) -> dict:
        return self._s.request(
            "POST", f"{BASE}/courses/{course_id}/studentGroups", json={"title": title}
        )

    def list_group_members(self, course_id: str, group_id: str) -> list[dict]:
        url = f"{BASE}/courses/{course_id}/studentGroups/{group_id}/studentGroupMembers"
        return list(self._s.paginate(url, "studentGroupMembers"))

    def add_group_member(self, course_id: str, group_id: str, user_id: str) -> dict:
        url = f"{BASE}/courses/{course_id}/studentGroups/{group_id}/studentGroupMembers"
        return self._s.request("POST", url, json={"userId": user_id})

    def remove_group_member(self, course_id: str, group_id: str, user_id: str) -> None:
        url = f"{BASE}/courses/{course_id}/studentGroups/{group_id}/studentGroupMembers/{user_id}"
        self._s.request("DELETE", url)
