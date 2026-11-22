"""The interfaces the pipeline talks to, and the normalized shapes it gets back.

The pipeline never touches Google directly. It drives a *forms backend* and a
*classroom backend*, each of which hides whether it uses the official OAuth
APIs or the reverse-engineered web protocol. Swap a backend and the lessons,
students and answers logic above is unchanged.

Backends that exist today:

* Forms: :class:`google_classroom_api.oauth_backends.OAuthFormsBackend`
  (official Forms API). There is no headless web forms backend — Google binds
  the Docs/Forms session to the device, so exported cookies cannot drive the
  form editor off the browser.
* Classroom: :class:`google_classroom_api.oauth_backends.OAuthClassroomBackend`
  (official API; assigns to named students, reads rosters and submissions) or
  :class:`google_classroom_api.web.WebClassroomBackend` (cookies; adds the
  "default grade for missing work" setting the official API lacks, but cannot
  assign to named students).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Protocol, Sequence, runtime_checkable


# --- normalized data the pipeline consumes --------------------------------------

@dataclass
class ResolvedSlot:
    """One managed form item after a sync, with the ids the store should keep."""

    slot_key: str
    kind: str
    site_question_id: Optional[str]
    item_id: Optional[str]
    question_id: Optional[str]
    content_hash: str
    snapshot: Optional[dict]
    is_new: bool
    changed: bool


@dataclass
class FormSync:
    """What a forms backend did and what the store should record."""

    form_id: str
    responder_url: str
    slots: list[ResolvedSlot]
    deleted_keys: list[str]
    ops_sent: int
    created: bool
    dropped_settings: list[str] = field(default_factory=list)


@dataclass
class ResponseMeta:
    response_id: str
    email: str
    submitted_ms: int
    last_submitted_ms: int


@dataclass
class ResponseData:
    response_id: str
    email: str
    submitted_ms: int
    timestamps_ms: list[int]
    answers: dict[str, list[str]]           # question_id -> raw values
    scores: dict[str, dict[str, Any]]       # question_id -> {max_points, earned_points, correct}
    total: Optional[dict[str, Any]] = None  # {max_points, earned_points}


@dataclass
class StudentInfo:
    user_id: str
    email: Optional[str]
    full_name: Optional[str]


@dataclass
class SubmissionInfo:
    user_id: str
    state: str                       # NEW | CREATED | TURNED_IN | RETURNED | RECLAIMED_BY_STUDENT ...
    late: bool = False
    assigned_grade: Optional[float] = None
    draft_grade: Optional[float] = None
    update_time: Optional[str] = None
    history: list = field(default_factory=list)


@dataclass
class AssignmentResult:
    coursework_id: str
    state: str                       # "PUBLISHED" | "DRAFT"
    alternate_link: Optional[str] = None
    attachment_id: Optional[str] = None


# --- backend protocols ----------------------------------------------------------

@runtime_checkable
class FormsBackend(Protocol):
    def prepare_form(self, form_id: Optional[str], lesson, known: dict) -> FormSync:
        """Create the form (if ``form_id`` is None) and make it match ``lesson``.

        ``known`` maps a slot key to the stored row (``item_id``,
        ``question_id``, ``kind``, ``content_hash``). Only missing or changed
        items are sent; existing questions keep their ids. Returns the resolved
        slots (with the ids new items were given), the responder URL, and the
        settings this backend could not honour.
        """

    def set_accepting_responses(self, form_id: str, accepting: bool) -> None: ...

    def list_responses(self, form_id: str) -> list[ResponseMeta]: ...

    def get_response(self, form_id: str, response_id: str) -> ResponseData: ...


@runtime_checkable
class ClassroomBackend(Protocol):
    #: Whether :meth:`set_missing_work_grade` does anything on this backend.
    supports_missing_work_grade: bool
    #: Whether :meth:`create_assignment` honours ``student_ids``.
    supports_individual_students: bool

    def list_students(self, course_id: str) -> list[StudentInfo]: ...

    def ensure_topic(self, course_id: str, name: str) -> str: ...

    def create_assignment(
        self, course_id: str, *, title: str, instructions: str = "", points: int = 100,
        topic_id: Optional[str] = None, due: Optional[datetime] = None, due_has_time: bool = True,
        form_url: Optional[str] = None, student_ids: Optional[Sequence[str]] = None,
        publish: bool = True,
    ) -> AssignmentResult: ...

    def update_assignment(
        self, course_id: str, coursework_id: str, *, title: str, instructions: str = "",
        points: int = 100, topic_id: Optional[str] = None, due: Optional[datetime] = None,
        due_has_time: bool = True, form_url: Optional[str] = None,
        student_ids: Optional[Sequence[str]] = None, publish: bool = True,
    ) -> AssignmentResult: ...

    def list_submissions(self, course_id: str, coursework_id: str) -> list[SubmissionInfo]: ...

    def set_missing_work_grade(self, course_id: str, *, enabled: bool,
                               percent: Optional[float] = None) -> dict: ...


__all__ = [
    "ResolvedSlot", "FormSync", "ResponseMeta", "ResponseData", "StudentInfo", "SubmissionInfo",
    "AssignmentResult", "FormsBackend", "ClassroomBackend",
]
